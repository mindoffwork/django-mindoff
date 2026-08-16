import polars as pl
from django.db import connections, models
from django.db.models.fields.related import ForeignKey, OneToOneField

from ..polars_kit import mo_polars_kit
from ..validation_kit import mo_validation_kit
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
# Classes
# ----------------
class ForeignKeyValidator:
    """
    Validate foreign-key integrity across incoming model DataFrames.

    This validator ensures that every foreign-key value in each model frame points
    to an existing related primary key, either from another provided frame or from
    the database when the related model frame is not supplied.

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
                related_df = self.df_dict[related_model].select(
                    pl.col(related_pk_col).cast(pl.Utf8).alias(related_pk_col)
                )
                invalid_fk_df = (
                    df.select(pl.col(db_col).cast(pl.Utf8).alias(db_col))
                    .drop_nulls()
                    .join(
                        related_df, left_on=db_col, right_on=related_pk_col, how="anti"
                    )
                )
                is_invalid_fk = mo_polars_kit.get_frm_height(invalid_fk_df) > 0
            else:
                fk_values = self._get_distinct_fk_values(df, db_col)
                if fk_values.is_empty():
                    continue
                manager = self._related_manager(related_model)
                existing_count = self._count_existing_fks(
                    manager, related_pk_name, fk_values
                )
                is_invalid_fk = existing_count < fk_values.len()
            mo_validation_kit.ensure_falsey(
                is_invalid_fk,
                msg=f"Model '{model.__name__}' couldn't resolve foreign key(s) in column '{db_col}'.",
                is_exception=True,
            )
        return df

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

    def _count_existing_fks(self, manager, related_pk_name: str, fk_values) -> int:
        """Count how many of ``fk_values`` exist, querying in bounded chunks.

        The values are already distinct, so the chunks are disjoint and the
        per-chunk counts sum exactly. Stops at the first short chunk: the
        foreign key is already known to be unresolvable at that point, so the
        remaining round-trips would be wasted work — which makes the failing
        case faster than the single-statement version this replaces.
        """
        chunk_size = self._fk_chunk_size(manager)
        total = 0
        for offset in range(0, fk_values.len(), chunk_size):
            chunk = fk_values.slice(offset, chunk_size).to_list()
            found = manager.filter(**{f"{related_pk_name}__in": chunk}).count()
            total += found
            if found < len(chunk):
                break
        return total

    def _get_distinct_fk_values(
        self, df: pl.DataFrame | pl.LazyFrame, col: str
    ) -> pl.Series:
        """Distinct non-null FK values, as a Series rather than a list.

        Deliberately not ``.to_list()``: the whole distinct set would become one
        Python object per value, which for a frame keyed by UUID dwarfs the
        Arrow buffer holding the same data. Only a chunk at a time is converted,
        in :meth:`_count_existing_fks`.
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
