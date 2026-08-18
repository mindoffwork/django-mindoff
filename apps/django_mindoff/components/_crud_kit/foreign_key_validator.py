from itertools import islice

import polars as pl
from django.db import connections, models
from django.db.models.fields.related import ForeignKey, OneToOneField

from ..polars_kit import mo_polars_kit
from django.conf import settings

# ----------------
# Constants
# ----------------
ERROR_COL = getattr(settings, "POLARS_VALIDATOR_ERROR_COL", None) or "__error__info"

# Upper bound on how many foreign-key values go into a single ``IN (...)``.
#
# Backends that advertise a real parameter limit are honored exactly: SQLite's
# ``SQLITE_MAX_VARIABLE_NUMBER`` (32766 by default, lower on some builds) is a
# hard wall — exceeding it raises "too many SQL variables". psycopg2 and MySQL
# report no limit at all, because they interpolate parameters client-side, so
# for them this ceiling is the only thing standing between a frame with millions
# of distinct keys and a single enormous statement.
#
# Override with ``MO_CRUD_FK_CHUNK_SIZE``.
DEFAULT_FK_CHUNK_SIZE = 10_000


# ----------------
# Functions
# ----------------
def _normalize_pk(value) -> str:
    """Canonical text form of a primary-key value, for cross-source comparison.

    The frame carries keys as text — dashless and lowercase once ``RowValidator``
    has normalized a UUID column — while the ORM hands back ``UUID`` (or ``int``)
    objects. Both sides go through here, so the comparison is between like and
    like whichever path produced the value.
    """
    return str(value).replace("-", "").lower()


# ----------------
# Classes
# ----------------
class ForeignKeyValidator:
    """
    Validate foreign-key integrity across incoming model DataFrames.

    This validator ensures that every foreign-key value in each model frame points
    to an existing related primary key, either from another provided frame or from
    the database when the related model frame is not supplied.

    An unresolvable reference is reported the way every other validator reports a
    bad row: the offending row is marked in ``ERROR_COL`` and the frame is handed
    on intact, so ``_ModelFrmsValidInvalidSplitter`` classifies it alongside the
    rows that passed. This pass therefore never decides the fate of the batch —
    ``is_partial`` does, exactly as it does for a row-level failure.

    ``target`` is the database the rows are being written to. It matters only for
    the database-backed branch: a reference must exist where the rows are going,
    not wherever the default routing happens to point.
    """
    def __init__(
        self,
        df_dict: dict[type[models.Model], pl.DataFrame | pl.LazyFrame],
        target=None,
    ):
        self.df_dict = df_dict
        self.target = target
        # Keys already proven to exist, per (related model, database alias).
        # Scoped to this instance, which the pipeline builds fresh per call, so
        # a cached "exists" can never outlive the read that established it.
        self._existing_memo: dict[tuple, set[str]] = {}

    def validate(self) -> dict[type[models.Model], pl.DataFrame | pl.LazyFrame]:
        """
        Validate foreign keys for all model frames and return validated frames.
        """
        return {
            model: self._validate_model_foreign_keys(model, df)
            for model, df in self.df_dict.items()
        }

    def _validate_model_foreign_keys(self, model, df):
        if mo_polars_kit.is_frm_empty(df):
            return df
        # Resolve column names once (lazy-safe). Adding ERROR_COL below does not
        # affect FK-column membership, so this list stays valid for the loop.
        column_names = mo_polars_kit.resolve_schema(df).names()
        if ERROR_COL not in column_names:
            df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(ERROR_COL))
        for field in model._meta.concrete_fields:
            if not isinstance(field, (ForeignKey, OneToOneField)):
                continue

            db_col = field.db_column
            if db_col not in column_names:
                # The frame does not carry this foreign key, so there is no
                # reference to check. ``update()`` writes only the columns it was
                # given, and an omitted one keeps whatever the row already holds
                # — a value the database already validated. ``create()`` never
                # reaches here with a gap: ``ColumnValidator`` fills or rejects
                # incomplete frames before this pass runs.
                continue

            related_model = field.related_model
            related_pk_field = related_model._meta.pk
            related_pk_col = (
                related_pk_field.db_column or related_pk_field.attname or "id"
            )
            related_pk_name = related_pk_field.attname
            if related_model in self.df_dict:
                df = self._flag_unresolved_against_frm(
                    model, df, db_col, related_model, related_pk_col
                )
            else:
                df = self._flag_unresolved_against_db(
                    model, df, db_col, related_model, related_pk_name
                )
        return df

    def _flag_unresolved_against_frm(
        self, model, df, db_col, related_model, related_pk_col
    ):
        """Mark rows whose reference is absent from the related frame.

        A left join against the distinct related keys, rather than a row-by-row
        membership test: the keys stay inside the engine, so no value is ever
        materialized into Python and a ``LazyFrame`` picks the marking up as plan
        nodes instead of being executed here. The anti-join this replaces ended
        in a row count, which collected once per foreign-key column — cheap for
        an in-memory frame, since projection pushdown narrows it to the one
        column, but a repeated read for a scan-backed frame.

        Marking every row costs more than the single boolean that count produced
        — measured at roughly +80 MB and +0.14 s for a million rows across four
        foreign-key columns. That is the price of per-row classification, not a
        regression: the old check could not say *which* rows were bad.
        ``maintain_order`` is not part of that price; it measured cheaper than
        letting the join reorder, and it keeps ``invalid_model_frms`` in input
        order.
        """
        key_col = f"__fk_key__{db_col}"
        found_col = f"__fk_found__{db_col}"
        related_keys = (
            self.df_dict[related_model]
            .select(pl.col(related_pk_col).cast(pl.Utf8).unique().alias(key_col))
            .with_columns(pl.lit(True).alias(found_col))
        )
        related_keys = self._match_frm_kind(df, related_keys)
        df = df.with_columns(pl.col(db_col).cast(pl.Utf8).alias(key_col)).join(
            related_keys, on=key_col, how="left", maintain_order="left"
        )
        # A null reference is not an unresolved one: nothing was pointed at, and
        # required-ness is the row pass's call rather than this one's.
        df = df.with_columns(
            pl.when(pl.col(key_col).is_not_null() & pl.col(found_col).is_null())
            .then(self._append_or_initialize_error(model, db_col, related_model))
            .otherwise(pl.col(ERROR_COL))
            .alias(ERROR_COL)
        )
        return df.drop([key_col, found_col], strict=False)

    def _flag_unresolved_against_db(
        self, model, df, db_col, related_model, related_pk_name
    ):
        """Mark rows whose reference is absent from the database."""
        fk_values = self._get_distinct_fk_values(df, db_col)
        if fk_values.is_empty():
            return df
        manager = self._related_manager(related_model)
        missing_values = self._get_missing_fk_values(
            manager, related_pk_name, fk_values, related_model
        )
        if missing_values.is_empty():
            return df
        return df.with_columns(
            pl.when(pl.col(db_col).cast(pl.Utf8).is_in(missing_values.implode()))
            .then(self._append_or_initialize_error(model, db_col, related_model))
            .otherwise(pl.col(ERROR_COL))
            .alias(ERROR_COL)
        )

    def _match_frm_kind(self, df, other):
        """Align ``other`` with ``df``'s laziness so the two can be joined.

        Polars refuses to join across the eager/lazy boundary. Widening the eager
        side is free; narrowing a lazy one collects the distinct key column alone,
        never the frame it came from.
        """
        if isinstance(df, pl.LazyFrame) and isinstance(other, pl.DataFrame):
            return other.lazy()
        if isinstance(df, pl.DataFrame) and isinstance(other, pl.LazyFrame):
            return other.collect(engine="streaming")
        return other

    def _related_manager(self, related_model):
        """Manager for the related model, routed to the write target if named.

        Resolved per lookup rather than up front, so a frame whose related models
        are all supplied in-memory never requires the target to be reachable by
        the ORM at all.
        """
        manager = related_model.objects
        if self.target is None:
            return manager
        alias = self.target.orm_alias(operation="Foreign-key validation")
        return manager if alias is None else manager.using(alias)

    def _fk_chunk_size(self, manager) -> int:
        """Largest number of FK values safe to put in one ``IN (...)``.

        Read from the backend rather than guessed: Django exposes the real
        parameter ceiling as ``connection.features.max_query_params``, which is
        a number on SQLite and ``None`` on the client-side-binding drivers. The
        local ceiling applies either way, so the ``None`` backends still chunk,
        and the smaller of the two always wins.

        ``MO_CRUD_FK_CHUNK_SIZE`` tunes the local ceiling. Unlike
        ``MO_CRUD_ENGINE_CACHE_SIZE``, zero is not an opt-out: an unbounded
        ``IN (...)`` is a hard error on SQLite and an unbounded statement
        everywhere else, so a non-positive value falls back to the default.
        """
        ceiling = getattr(settings, "MO_CRUD_FK_CHUNK_SIZE", DEFAULT_FK_CHUNK_SIZE)
        if not ceiling or ceiling <= 0:
            ceiling = DEFAULT_FK_CHUNK_SIZE
        try:
            limit = connections[manager.db].features.max_query_params
        except Exception:  # pragma: no cover - defensive: exotic router/alias
            limit = None
        return min(ceiling, limit or ceiling)

    def _get_missing_fk_values(
        self, manager, related_pk_name: str, fk_values, related_model
    ) -> pl.Series:
        """Which of ``fk_values`` the database does not hold, queried in chunks.

        Marking a row needs the values that are missing, not a count of them, so
        every chunk is queried — the short-circuit on the first short chunk that a
        bare count could afford would leave the later chunks' bad rows unflagged.
        Correctness across chunk boundaries comes free: the values are already
        distinct, so the chunks are disjoint and a value absent from its own
        chunk's answer is absent outright.

        Only one chunk is ever resident. The chunk's existing keys are consumed
        straight off an ``.iterator()`` into a set that dies with the iteration,
        so the full existing set never accumulates — what accumulates is the
        missing values alone, which a healthy frame leaves empty.

        Keys proven to exist are remembered for the rest of the call, so a second
        foreign key pointing at the same table does not re-ask about values this
        one already resolved: they drop out of the next ``IN (...)``, shrinking
        both the round trips and the bytes on the wire. The memo is capped at one
        chunk per related model — remembering every existing key would rebuild
        exactly the full existing set this method is written to avoid, so once
        the cap is reached the remainder is simply not remembered and behavior
        degrades to querying it again.
        """
        chunk_size = self._fk_chunk_size(manager)
        memo_key = (related_model, manager.db)
        known = self._existing_memo.setdefault(memo_key, set())
        missing: list[str] = []
        for offset in range(0, fk_values.len(), chunk_size):
            chunk = [
                value
                for value in fk_values.slice(offset, chunk_size).to_list()
                if _normalize_pk(value) not in known
            ]
            if not chunk:
                continue
            found = {
                _normalize_pk(value)
                for value in manager.filter(**{f"{related_pk_name}__in": chunk})
                .values_list(related_pk_name, flat=True)
                .iterator()
            }
            missing.extend(value for value in chunk if _normalize_pk(value) not in found)
            room = chunk_size - len(known)
            if room > 0:
                known.update(islice(found, room))
            del found, chunk
        return pl.Series("__fk_missing__", missing, dtype=pl.Utf8)

    def _get_distinct_fk_values(
        self, df: pl.DataFrame | pl.LazyFrame, col: str
    ) -> pl.Series:
        """Distinct non-null FK values, as a Series rather than a list.

        Deliberately not ``.to_list()``: the whole distinct set would become one
        Python object per value, which for a frame keyed by UUID dwarfs the
        Arrow buffer holding the same data. Only a chunk at a time is converted,
        in :meth:`_get_missing_fk_values`.
        """
        try:
            df_fk = df.select(pl.col(col).drop_nulls().cast(pl.Utf8).unique())
            if isinstance(df, pl.LazyFrame):
                return df_fk.collect(engine="streaming").get_column(col)
            return df_fk.get_column(col)
        except Exception as e:
            raise ValueError(
                f"Error extracting unique FK values from column '{col}': {e}"
            )

    def _append_or_initialize_error(self, model, db_col, related_model) -> pl.Expr:
        """Add this pass's message without discarding one already on the row.

        Same shape as ``RowValidator._append_or_initialize_error``: a row that
        already failed the row pass keeps that text and gains this one, so the
        error column reads as the full list of what is wrong with the row.
        """
        message = self._response_messages(
            error_key="unresolved_foreign_key",
            context=f"{model.__name__}.{db_col}",
            exception=f"related={related_model.__name__}",
        )
        return (
            pl.when(pl.col(ERROR_COL).is_not_null())
            .then(pl.col(ERROR_COL) + pl.lit(message))
            .otherwise(pl.lit(message))
        )

    def _response_messages(
        self, error_key: str, context: str = "", exception: str = ""
    ) -> str:
        messages = {
            "unresolved_foreign_key": f"[{context}] Couldn't resolve foreign key: {exception}",
        }
        message = messages.get(error_key, "")
        output = message + " ; " if message else ""
        return output
