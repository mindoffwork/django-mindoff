"""
Mindoff CRUD Kit
1. mo_crud_kit.create
2. mo_crud_kit.update
3. mo_crud_kit.read
"""

import warnings
from typing import Any, Dict, List, Literal, Tuple, Type, Union
import polars as pl
from typeguard import typechecked
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db import models
from ._crud_kit import arrow_reader
from ._crud_kit.column_validator import ColumnValidator
from ._crud_kit.crud_processor import CRUDProcessor
from ._crud_kit.db_target import DbTargetLike, resolve_db_target
from ._crud_kit.foreign_key_validator import ForeignKeyValidator
from ._crud_kit.row_validator import RowValidator
from .polars_kit import mo_polars_kit
from .validation_kit import mo_validation_kit

# ----------------
# Constants
# ----------------
ERROR_COL = getattr(settings, "POLARS_VALIDATOR_ERROR_COL", None) or "__error__info"


# --------------
# Classes
# --------------
class MindoffCRUDHandler:
    """Model-aware bulk data operations over Polars frames.

    Exposes high-throughput CRUD-style helpers that operate on
    `{DjangoModel: DataFrame|LazyFrame}` mappings.

    Available methods:

    - `mo_crud_kit.create(...)`
    - `mo_crud_kit.read(...)`
    - `mo_crud_kit.update(...)`

    Note:

    - `delete()` is not exposed in `mo_crud_kit` yet.
    """

    @typechecked
    def create(
        self,
        model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        *,
        using: DbTargetLike = None,
        is_partial: bool = False,
        validation_level: Literal["full", "columns_only", "none"] = "full",
        batch_size: int = 1000,
        is_validate_only: bool = False,
    ) -> Tuple[str, Dict, Dict]:
        """Create rows from model-to-frame mappings with optional validation pipeline.

        Usage:

        ```python
        from django_mindoff import mo_crud_kit

        status, valid_model_frms, invalid_model_frms = mo_crud_kit.create(
            {
                OrderModel: order_df,
                OrderItemModel: order_item_df,
            },
            is_partial=False,
            validation_level="full",
            batch_size=1000,
        )
        ```

        Parameters:

        - `model_frms` (`dict[type[models.Model], pl.DataFrame|pl.LazyFrame]`):
          Input model-frame mapping for bulk insert.
        - `using` (`str|BaseDatabaseWrapper|None, default=None`):
          Which database to write to. `None` targets the default database and
          leaves database routing untouched. Pass an alias to target another
          configured database, or a live Django connection object to target one
          provisioned at runtime whose credentials were never written into
          `settings.DATABASES`. See the note on dynamic databases below.
        - `is_partial` (`bool, default=False`):
          If `True`, allows partial save when only a subset of rows are valid
          (applies to `validation_level="full"` only).
        - `validation_level` (`"full"|"columns_only"|"none", default="full"`):
          Selects how much validation runs before the insert.
        - `batch_size` (`int, default=1000`):
          Maximum number of rows written per INSERT batch. Has no meaning when
          `is_validate_only=True`: nothing is batched because nothing is written.
        - `is_validate_only` (`bool, default=False`):
          If `True`, runs the validation pipeline and returns its verdict without
          writing anything — a preview of what a real `create()` would classify.

        Varieties:

        - `validation_level="full"` (default): runs
          `ColumnValidator -> RowValidator -> ForeignKeyValidator` and honors
          `is_partial`.
        - `validation_level="columns_only"`: runs only `ColumnValidator` to
          normalize frame shape (rename to `db_column`, add missing/auto columns,
          drop extras) and writes directly, trusting the caller's row values and
          foreign keys. Row-level passes are skipped, so field defaults,
          `auto_now`/`auto_now_add` timestamps, UUID generation, and type coercion
          are NOT applied — the caller must supply database-ready values for every
          required column. `is_partial` does not apply (no per-row invalidation).
        - `validation_level="none"`: skips all validation (including column
          normalization) and writes the frames as-is; emits an unsafe-write
          warning.
        - Partial-save mode (full only):
          `is_partial=False` fails if any invalid rows exist;
          `is_partial=True` saves valid rows and returns invalid rows separately.
        - `is_validate_only=True`: a dry run at any `validation_level`. The
          selected validation runs exactly as it would for a real create —
          including the foreign-key existence queries at `"full"`, which are
          reads — and then the write is skipped entirely. Nothing is written even
          at `"none"` or `"columns_only"`, which otherwise write directly.

        Possible responses:

        - Returns `("ok", valid_model_frms, {})` when all rows are valid and inserted.
        - Returns `("partial_ok", valid_model_frms, invalid_model_frms)` when partial mode is enabled and some rows are invalid.
        - Returns `("fail", valid_model_frms, invalid_model_frms)` when validation fails and no write should proceed.

        Notes:

        - Invalid rows contain an error column (`POLARS_VALIDATOR_ERROR_COL` or `__error__info`).
        - An unresolvable foreign key is an invalid row like any other: the row is
          marked in the error column and returned in `invalid_model_frms`, so
          `is_partial=True` writes the rest of the batch and `is_partial=False`
          fails the call without writing. It does not raise.
        - `is_validate_only=True` returns the same
          `(status, valid_model_frms, invalid_model_frms)` shape as a real create,
          so it is a drop-in preview of one. What it cannot report is anything the
          write itself would raise: the database is never contacted for the write,
          so integrity errors, unsupported-backend errors, and connection failures
          surface only on the real call.
        - `validation_level="none"` still emits its unsafe-write warning under
          `is_validate_only=True`. Nothing is written, so the warning is not about
          this call — it is the only signal that the preview checked nothing at
          all, and therefore says nothing about the write it stands in for.
        - `validation_level="none"` skips safety checks and may persist unsafe data.
        - Rows are written with each backend's fastest same-transaction bulk
          loader — PostgreSQL `COPY`, MySQL `LOAD DATA LOCAL INFILE` (auto-falling
          back to row binding when the server forbids it), SQLite `executemany` —
          so the columnar frame reaches the database without a per-row Python
          detour, and the whole create stays atomic.
        - Dynamic databases: a connection passed as `using` does not have to
          appear in `settings.DATABASES`, but foreign-key validation issues ORM
          queries, so at `validation_level="full"` the connection must also be
          registered in `django.db.connections` under its alias. If it is not,
          use `validation_level="columns_only"`.
        """
        model_frms = mo_polars_kit.sync_model_frms_type(model_frms)
        target = resolve_db_target(using)

        status, valid_model_frms, invalid_model_frms = _run_validation_pipeline(
            validation_level,
            model_frms,
            is_partial=is_partial,
            target=target,
            none_warning=(
                "Saving without validation may store unsafe or inconsistent data, "
                "which can affect further read/update/delete functions. "
                "Proceed only if intentional."
            ),
        )
        if status == "fail":
            return "fail", valid_model_frms, invalid_model_frms
        if is_validate_only:
            # Dry run: the caller wants the classification, not the rows. Return
            # before ``CRUDProcessor`` is even constructed — building one opens a
            # connection and mutates the process-wide engine cache, neither of
            # which a preview has any business doing.
            return status, valid_model_frms, invalid_model_frms

        # Perform CRUD operation
        crud_processor = CRUDProcessor(valid_model_frms, using=target)
        _ = crud_processor.create(batch_size=batch_size)
        return status, valid_model_frms, invalid_model_frms

    @typechecked
    def read(
        self,
        qs: models.QuerySet,
        *,
        page_number: int | None = None,
        is_lazy: bool = False,
        batch_size: int = 0,
        json_column_mode: Literal["auto", "object", "text"] = "auto",
        with_stats: bool = True,
    ) -> tuple[pl.DataFrame | pl.LazyFrame, dict[str, Any]]:
        """Read queryset data into Polars with streaming/pagination variants.

        Usage:

        ```python
        from django_mindoff import mo_crud_kit

        frm, stats = mo_crud_kit.read(
            OrderModel.objects.filter(is_active=True).values(),
            page_number=1,
            is_lazy=False,
            batch_size=100,
        )
        ```

        Parameters:

        - `qs` (`models.QuerySet`):
          Queryset that must use `.values()` output.
        - `page_number` (`int|None, default=None`):
          When provided, enables pagination mode.
          When `None`, uses streaming mode.
        - `is_lazy` (`bool, default=False`):
          If `True`, returns `pl.LazyFrame`; otherwise returns `pl.DataFrame`.
          In streaming mode with the `auto`/`connectorx` engines this is a real
          larger-than-RAM scan: rows are streamed to a temporary Parquet file and
          the returned `LazyFrame` scans it lazily (the temp file is removed when
          the frame is garbage-collected). The `iterator` engine keeps the legacy
          deferred-`.lazy()` behavior.
        - `batch_size` (`int, default=0`):
          Chunk/page size. Auto-resolved when `0`. Drives the lazy-scan/streaming
          fetch size and pagination; eager fast-path reads fetch in one transfer.
        - `with_stats` (`bool, default=True`):
          When `True`, computes `total_count`/`total_pages` (and the empty-set
          short-circuit) via extra `exists()`/`count()` queries. Set `False` for
          the fastest read: those queries are skipped, `total_count`/`total_pages`
          are `None`, and pagination derives `has_next` by fetching one extra row.
        - `json_column_mode` (`"auto"|"object"|"text", default="auto"`):
          How `JSONField` columns are returned. `text` keeps raw JSON text
          (`Utf8`); `object` parses to `pl.Object`; `auto` resolves to `text`.

        Varieties:

        - Streaming mode (`page_number=None`): reads the full dataset.
        - Pagination mode (`page_number=<n>`): reads one page and returns paging metadata.
        - Materialization mode: eager (`DataFrame`) or lazy (`LazyFrame`).

        Possible responses:

        - Returns `(frm, stats)` where `frm` is `DataFrame`/`LazyFrame` and `stats` includes:
          `mode`, `batch_size`, `total_count`, `total_pages`, `current_page`, `has_next`, `has_previous`.
        - Returns empty frame with zeroed stats for empty querysets.
        - Raises validation error if queryset is not `.values()`-based.

        Note:

        - Frames are normalized to the canonical model dtypes
          (`_crud_kit.dtypes.DJANGO_TO_POLARS_TYPE_MAP`), the same mapping the
          create/update validators enforce, so a read can be fed straight back
          into `update`.
        """
        # 1. Validate and Normalize
        mo_validation_kit.ensure(
            issubclass(qs._iterable_class, models.query.ValuesIterable),
            msg=f"Unsupported queryset type `{qs._iterable_class.__name__}`. "
            "You must call `.values()` on the queryset before passing it to `read()`.",
            is_exception=True,
        )
        mo_validation_kit.ensure_greater_equal(
            batch_size,
            0,
            msg=f"The 'batch_size' parameter must be a positive integer. Provided value: {batch_size}",
            is_exception=True,
        )
        if batch_size == 0:
            batch_size = 100 if page_number else 1000

        # 2. Empty queryset short-circuit (stats mode only; relies on exists()).
        if with_stats and not qs.exists():
            frm = pl.DataFrame([]).lazy() if is_lazy else pl.DataFrame([])
            stats = _read__build_stats(
                mode="pagination" if page_number else "streaming",
                batch_size=batch_size,
                total_count=0,
                total_pages=0,
                current_page=page_number if page_number else 0,
                has_next=False,
                has_previous=False,
            )
            return frm, stats

        # 3. Streaming mode
        if not page_number:
            return _read__stream_mode(
                qs,
                batch_size=batch_size,
                is_lazy=is_lazy,
                json_column_mode=json_column_mode,
                with_stats=with_stats,
            )

        # 4. Pagination mode
        if not with_stats:
            return _read__paginate_no_stats(
                qs,
                page_number=page_number,
                batch_size=batch_size,
                is_lazy=is_lazy,
                json_column_mode=json_column_mode,
            )
        return _read__paginate_with_stats(
            qs,
            page_number=page_number,
            batch_size=batch_size,
            is_lazy=is_lazy,
            json_column_mode=json_column_mode,
        )

    @typechecked
    def read_batches(
        self,
        qs: models.QuerySet,
        *,
        batch_size: int = 0,
        json_column_mode: Literal["auto", "object", "text"] = "auto",
    ):
        """Stream a queryset as an iterator of Polars frames (memory-bounded).

        Usage:

        ```python
        from django_mindoff import mo_crud_kit

        for frm in mo_crud_kit.read_batches(
            OrderModel.objects.filter(is_active=True).values(),
            batch_size=10_000,
        ):
            process(frm)
        ```

        Parameters:

        - `qs` (`models.QuerySet`):
          Queryset that must use `.values()` output.
        - `batch_size` (`int, default=0`):
          Rows per yielded frame. Auto-resolved to `1000` when `0`.
        - `json_column_mode` (`"auto"|"object"|"text", default="auto"`):
          JSON handling, identical to `read()`. Each batch is streamed from
          Django's cursor and normalized to the canonical model dtypes.

        Possible responses:

        - Returns a generator of `pl.DataFrame` chunks; never concatenated, so
          the full result set is never held in memory at once.
        - Raises validation error if queryset is not `.values()`-based.
        """
        mo_validation_kit.ensure(
            issubclass(qs._iterable_class, models.query.ValuesIterable),
            msg=f"Unsupported queryset type `{qs._iterable_class.__name__}`. "
            "You must call `.values()` on the queryset before passing it to `read_batches()`.",
            is_exception=True,
        )
        mo_validation_kit.ensure_greater_equal(
            batch_size,
            0,
            msg=f"The 'batch_size' parameter must be a positive integer. Provided value: {batch_size}",
            is_exception=True,
        )
        if batch_size == 0:
            batch_size = 1000
        return arrow_reader.stream_batches(
            qs,
            batch_size=batch_size,
            json_column_mode=json_column_mode,
        )

    @typechecked
    def update(
        self,
        model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        *,
        using: DbTargetLike = None,
        is_partial: bool = False,
        validation_level: Literal["full", "columns_only", "none"] = "full",
        batch_size: int = 1000,
        is_validate_only: bool = False,
        skip_db_fill: bool = False,
    ):
        """Upsert rows from model-to-frame mappings via a staged merge.

        Usage:

        ```python
        from django_mindoff import mo_crud_kit

        status, valid_model_frms, invalid_model_frms = mo_crud_kit.update(
            {
                OrderModel: order_updates_df,
            },
            is_partial=True,
            validation_level="full",
            batch_size=1000,
        )
        ```

        Parameters:

        - `model_frms` (`dict[type[models.Model], pl.DataFrame|pl.LazyFrame]`):
          Input model-frame mapping for bulk update/upsert.
        - `using` (`str|BaseDatabaseWrapper|None, default=None`):
          Which database to write to. `None` targets the default database and
          leaves database routing untouched. Pass an alias to target another
          configured database, or a live Django connection object to target one
          provisioned at runtime whose credentials were never written into
          `settings.DATABASES`. See the note on dynamic databases below.
        - `is_partial` (`bool, default=False`):
          If `True`, allows valid rows to proceed even when invalid rows exist
          (applies to `validation_level="full"` only).
        - `validation_level` (`"full"|"columns_only"|"none", default="full"`):
          Selects how much validation runs before the upsert.
        - `batch_size` (`int, default=1000`):
          Batch size used to load the staging table. Has no meaning when
          `is_validate_only=True`: there is no staging table to load because
          nothing is written.
        - `is_validate_only` (`bool, default=False`):
          If `True`, runs the validation pipeline and returns its verdict without
          writing anything — a preview of what a real `update()` would classify.
        - `skip_db_fill` (`bool, default=False`):
          Deprecated and inert; scheduled for removal in 1.0. It used to skip a
          prefetch `SELECT` that back-filled the columns a frame omitted. There
          is no longer a back-fill to skip — omitted columns are simply not
          written — so passing it changes nothing and emits a
          `DeprecationWarning`.

        Varieties:

        - `validation_level="full"` (default): runs
          `ColumnValidator -> RowValidator -> ForeignKeyValidator` and honors
          `is_partial`.
        - `validation_level="columns_only"`: runs only `ColumnValidator` and
          upserts directly, trusting the caller's row values and foreign keys.
          Row-level passes are skipped, so the caller must supply
          database-ready values.
        - `validation_level="none"`: skips all validation; upserts as-is and
          emits an unsafe-write warning.
        - `is_validate_only=True`: a dry run at any `validation_level`. The
          selected validation runs exactly as it would for a real update —
          including the foreign-key existence queries at `"full"`, which are
          reads — and then the upsert is skipped entirely. Nothing is written even
          at `"none"` or `"columns_only"`, which otherwise write directly.

        Possible responses:

        - Returns `("ok", valid_model_frms, {})` when all rows are valid and updated.
        - Returns `("partial_ok", valid_model_frms, invalid_model_frms)` when partial mode is enabled and some rows are invalid.
        - Returns `("fail", valid_model_frms, invalid_model_frms)` when validation blocks update.

        Notes:

        - **Only the columns your frame carries are written.** A column the frame
          omits is left out of both the INSERT list and the `SET` clause, so an
          existing row keeps whatever it already holds and a new row takes the
          column's database default. Nothing is read back first, so two writers
          updating different columns of the same row no longer overwrite each
          other. The returned frames therefore carry exactly the columns that
          were written.
        - A frame carrying *only* the primary key has nothing to write; that is a
          no-op and emits a `RuntimeWarning` rather than failing silently.
        - Because `update()` is an upsert, a primary key that does not exist yet
          is an INSERT. If the frame omits a `NOT NULL` column that has no
          database default, that INSERT fails with the database's own integrity
          error and the whole call rolls back — use `create()` for genuinely new
          rows, or supply the column.
        - Invalid rows include model-aware error details in error column.
        - An unresolvable foreign key is an invalid row like any other: the row is
          marked in the error column and returned in `invalid_model_frms`, so
          `is_partial=True` merges the rest of the batch and `is_partial=False`
          fails the call without writing. It does not raise.
        - `is_validate_only=True` returns the same
          `(status, valid_model_frms, invalid_model_frms)` shape as a real update,
          so it is a drop-in preview of one. What it cannot report is anything the
          merge itself would surface: the database is never contacted for the
          write, so the PK-only no-op warning above, integrity errors from an
          upsert-insert, unsupported-backend errors, and connection failures
          appear only on the real call.
        - `validation_level="none"` still emits its unsafe-write warning under
          `is_validate_only=True`. Nothing is written, so the warning is not about
          this call — it is the only signal that the preview checked nothing at
          all, and therefore says nothing about the write it stands in for.
        - The staging merge loads the staging table with the same fast
          same-transaction bulk loader as `create()` (PostgreSQL `COPY`, MySQL
          `LOAD DATA LOCAL INFILE`, SQLite `executemany`) and then merges
          set-based in SQL, so no per-row Python materialization occurs.
        - Dynamic databases: a connection passed as `using` does not have to
          appear in `settings.DATABASES`. Foreign-key validation is the only
          stage that issues an ORM query, so either register the connection in
          `django.db.connections` under its alias or use
          `validation_level="columns_only"`.
        """
        if skip_db_fill:
            warnings.warn(
                "skip_db_fill is deprecated and no longer has any effect: update() "
                "writes only the columns the frame supplies, so there is no "
                "back-fill left to skip. It will be removed in 1.0.",
                DeprecationWarning,
                stacklevel=2,
            )
        target = resolve_db_target(using)
        model_frms, unset_columns = _update__prepare_frames(model_frms)

        status, valid_model_frms, invalid_model_frms = _run_validation_pipeline(
            validation_level,
            model_frms,
            is_partial=is_partial,
            target=target,
            allow_absent=unset_columns,
            none_warning=(
                "Updating without validation may store unsafe or inconsistent data, "
                "which can affect mindoff's read/update/delete functions. "
                "Proceed only if intentional."
            ),
        )
        # A dry run stops here: the caller wants the classification, not the
        # merge. ``CRUDProcessor`` is never constructed — building one opens a
        # connection and mutates the process-wide engine cache, neither of which
        # a preview has any business doing.
        if status != "fail" and not is_validate_only:
            # Perform CRUD operation
            crud_processor = CRUDProcessor(valid_model_frms, using=target)
            _ = crud_processor.update(batch_size=batch_size)
        return status, valid_model_frms, invalid_model_frms


# --------------
# Helper Classes
# --------------
class _ModelFrmsValidInvalidSplitter:
    def __init__(
        self, model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]]
    ):
        self.model_frms = self._normalize_frms(model_frms)
        self.relations = self._build_relations(list(self.model_frms.keys()))

    def run(self) -> Tuple[
        Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
    ]:
        invalid_ids = self._collect_initial_invalid_ids()
        invalid_ids = self._propagate_invalid_ids(invalid_ids)
        return self._split_valid_invalid(invalid_ids)

    def _normalize_frms(self, model_frms):
        normalized = {}
        for model, frm in model_frms.items():
            frm_schema = (
                frm.collect_schema() if isinstance(frm, pl.LazyFrame) else frm.schema
            )
            if ERROR_COL not in frm_schema:
                frm = frm.with_columns(pl.lit(None).alias(ERROR_COL))
            normalized[model] = frm
        return normalized

    def _build_relations(self, models_list: List[Type[models.Model]]):
        """Usable child->parent links for invalid-row propagation.

        A relation is only usable when both ends are actually reachable:
        propagation reads the FK column off the child frame and indexes
        ``invalid_ids`` by the parent model. So a foreign key the frame omits
        (``update()`` writes only the columns it was given) or one pointing at a
        model outside this operation is skipped — either would fault mid-walk.
        """
        relations = []
        in_batch = set(models_list)
        for model in models_list:
            columns = set(mo_polars_kit.resolve_schema(self.model_frms[model]).names())
            for f in model._meta.concrete_fields:
                if not isinstance(f, (models.ForeignKey, models.OneToOneField)):
                    continue
                fk_col = f.db_column or f.name
                if fk_col not in columns or f.related_model not in in_batch:
                    continue
                relations.append(
                    (
                        model,
                        fk_col,
                        f.related_model,
                        f.related_model._meta.pk.db_column
                        or f.related_model._meta.pk.name,
                    )
                )
        return relations

    def _collect_initial_invalid_ids(self):
        return {
            m: frm.filter(pl.col(ERROR_COL).is_not_null())
            .select(pl.col(m._meta.pk.db_column or m._meta.pk.name))
            .unique()
            for m, frm in self.model_frms.items()
        }

    def _propagate_invalid_ids(self, invalid_ids):
        queue = [
            m for m, ids in invalid_ids.items() if not mo_polars_kit.is_frm_empty(ids)
        ]
        visited = set()

        while queue:
            m = queue.pop()
            if m in visited:
                continue
            visited.add(m)

            for child, fk_col, parent, pk_col in self.relations:
                if m == child:
                    queue += self._propagate_to_parent(
                        invalid_ids, child, parent, fk_col, pk_col
                    )
                elif m == parent:
                    queue += self._propagate_to_child(
                        invalid_ids, child, parent, fk_col, pk_col
                    )

        return invalid_ids

    def _propagate_to_parent(self, invalid_ids, child, parent, fk_col, pk_col):
        new_invalid = (
            self.model_frms[child]
            .join(
                invalid_ids[child],
                on=child._meta.pk.db_column or child._meta.pk.name,
                how="inner",
            )
            .select(pl.col(fk_col).alias(pk_col))
            .unique()
        )
        before_empty = mo_polars_kit.is_frm_empty(invalid_ids[parent])
        invalid_ids[parent] = pl.concat([invalid_ids[parent], new_invalid]).unique()
        return (
            [parent]
            if not before_empty and not mo_polars_kit.is_frm_empty(new_invalid)
            else []
        )

    def _propagate_to_child(self, invalid_ids, child, parent, fk_col, pk_col):
        new_invalid = (
            self.model_frms[child]
            .join(invalid_ids[parent], left_on=fk_col, right_on=pk_col, how="inner")
            .select(pl.col(child._meta.pk.db_column or child._meta.pk.name))
            .unique()
        )

        before_empty = mo_polars_kit.is_frm_empty(invalid_ids[child])
        invalid_ids[child] = pl.concat([invalid_ids[child], new_invalid]).unique()
        return (
            [child]
            if not before_empty and not mo_polars_kit.is_frm_empty(new_invalid)
            else []
        )

    def _split_valid_invalid(self, invalid_ids):
        valid_model_frms, invalid_model_frms = {}, {}
        for m, frm in self.model_frms.items():
            pk = m._meta.pk.db_column or m._meta.pk.name
            bad_ids = invalid_ids[m]
            if mo_polars_kit.is_frm_empty(bad_ids):
                valid_model_frms[m] = frm.drop(ERROR_COL)
                invalid_model_frms[m] = frm.filter(pl.lit(False))
            else:
                frm_invalid = frm.join(bad_ids, on=pk, how="inner")
                frm_valid = frm.join(bad_ids, on=pk, how="anti").drop(ERROR_COL)
                valid_model_frms[m], invalid_model_frms[m] = frm_valid, frm_invalid

        return valid_model_frms, invalid_model_frms


# ----------------
# Helper Functions
# ----------------
def _read__collect(
    qs: models.QuerySet,
    *,
    batch_size: int,
    is_lazy: bool,
    json_column_mode: str,
) -> Union[pl.DataFrame, pl.LazyFrame]:
    """Materialize a full (streaming-mode) read."""
    if is_lazy:
        # Real larger-than-RAM scan: stream to disk and scan it lazily.
        return arrow_reader.scan_to_lazy(
            qs, batch_size=batch_size, json_column_mode=json_column_mode
        )
    return arrow_reader.read_frame(qs, json_column_mode=json_column_mode)


def _read__materialize(
    qs: models.QuerySet,
    *,
    is_lazy: bool,
    json_column_mode: str,
) -> Union[pl.DataFrame, pl.LazyFrame]:
    """Materialize a single page (pagination-mode)."""
    frm = arrow_reader.read_frame(qs, json_column_mode=json_column_mode)
    return frm.lazy() if is_lazy else frm


def _read__stream_mode(
    qs: models.QuerySet,
    *,
    batch_size: int,
    is_lazy: bool,
    json_column_mode: str,
    with_stats: bool,
) -> Tuple[Union[pl.DataFrame, pl.LazyFrame], dict[str, Any]]:
    """Streaming-mode read: full dataset plus streaming stats."""
    frm = _read__collect(
        qs,
        batch_size=batch_size,
        is_lazy=is_lazy,
        json_column_mode=json_column_mode,
    )
    stats = _read__build_stats(
        mode="streaming",
        batch_size=batch_size,
        total_count=qs.count() if with_stats else None,
        total_pages=0 if with_stats else None,
        current_page=0,
        has_next=False,
        has_previous=False,
    )
    return frm, stats


def _read__paginate_with_stats(
    qs: models.QuerySet,
    *,
    page_number: int,
    batch_size: int,
    is_lazy: bool,
    json_column_mode: str,
) -> Tuple[Union[pl.DataFrame, pl.LazyFrame], dict[str, Any]]:
    """Pagination-mode read with full paginator metadata (uses count())."""
    paginator = Paginator(qs, batch_size)
    try:
        page = paginator.page(page_number)
        frm = _read__materialize(
            page.object_list,
            is_lazy=is_lazy,
            json_column_mode=json_column_mode,
        )
        stats = _read__build_stats(
            mode="pagination",
            batch_size=batch_size,
            total_count=paginator.count,
            total_pages=paginator.num_pages,
            current_page=page.number,
            has_next=page.has_next(),
            has_previous=page.has_previous(),
        )
    except EmptyPage:
        frm = pl.DataFrame([]).lazy() if is_lazy else pl.DataFrame([])
        stats = _read__build_stats(
            mode="pagination",
            batch_size=batch_size,
            total_count=paginator.count,
            total_pages=paginator.num_pages,
            current_page=page_number,
            has_next=False,
            has_previous=page_number > 1,
        )
    return frm, stats


def _read__paginate_no_stats(
    qs: models.QuerySet,
    *,
    page_number: int,
    batch_size: int,
    is_lazy: bool,
    json_column_mode: str,
) -> Tuple[Union[pl.DataFrame, pl.LazyFrame], dict[str, Any]]:
    """Pagination-mode read without count(): `has_next` via one extra row."""
    offset = (page_number - 1) * batch_size
    sliced = qs[offset : offset + batch_size + 1]
    frm = arrow_reader.read_frame(sliced, json_column_mode=json_column_mode)
    has_next = frm.height > batch_size
    if has_next:
        frm = frm.head(batch_size)
    if is_lazy:
        frm = frm.lazy()
    stats = _read__build_stats(
        mode="pagination",
        batch_size=batch_size,
        total_count=None,
        total_pages=None,
        current_page=page_number,
        has_next=has_next,
        has_previous=page_number > 1,
    )
    return frm, stats


def _run_validation_pipeline(
    level: str,
    model_frms: Dict,
    *,
    is_partial: bool,
    none_warning: str,
    target=None,
    allow_absent: Dict | None = None,
):
    """Run the configured validation level and return ``(status, valid, invalid)``.

    Shared by ``create()`` and ``update()``. ``status`` is
    ``"ok"``/``"partial_ok"``/``"fail"``; a ``"fail"`` status means the caller
    must not write. ``"none"`` skips all validation (emitting ``none_warning``),
    ``"columns_only"`` runs only column normalization, and ``"full"`` runs the
    complete column -> row -> FK pipeline with valid/invalid splitting.

    ``target`` is the database the operation writes to; it reaches only the FK
    pass, which is the one stage that queries the database, so FK existence is
    checked where the rows are actually going.

    ``allow_absent`` names, per model, the db columns the frame may legitimately
    omit — ``update()``'s partial-column write. They are left absent rather than
    filled with NULL, which the row and FK passes already handle by skipping any
    field their frame does not carry.
    """
    if level == "none":
        warnings.warn(none_warning, RuntimeWarning)
        return "ok", model_frms, {}

    # Column validation runs for both "full" and "columns_only".
    valid_model_frms, invalid_model_frms = ColumnValidator(
        model_frms=model_frms,
        is_remove_extra_columns=True,
        is_add_missing_columns=True,
        allow_absent=allow_absent,
    ).run()
    if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
        return "fail", valid_model_frms, invalid_model_frms

    if level == "columns_only":
        # Caller guarantees clean row values + FKs: skip the row/FK passes.
        if mo_polars_kit.is_model_frms_empty(valid_model_frms):
            return "fail", valid_model_frms, {}
        return "ok", valid_model_frms, {}

    # "full": row validation -> FK validation -> valid/invalid split.
    row_validated_frms = RowValidator(valid_model_frms).run()
    fk_validated_frms = ForeignKeyValidator(row_validated_frms, target=target).validate()
    valid_model_frms, invalid_model_frms = _ModelFrmsValidInvalidSplitter(
        fk_validated_frms
    ).run()

    if mo_polars_kit.is_model_frms_empty(valid_model_frms):
        return "fail", valid_model_frms, invalid_model_frms
    if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
        if not is_partial:
            return "fail", valid_model_frms, invalid_model_frms
        return "partial_ok", valid_model_frms, invalid_model_frms
    return "ok", valid_model_frms, invalid_model_frms


def _canonical_uuid_expr(column: str) -> pl.Expr:
    """Normalize a UUID column to the stored dashless, lowercase form."""
    return (
        pl.col(column)
        .cast(pl.Utf8, strict=False)
        .str.to_lowercase()
        .str.replace_all("-", "")
        .alias(column)
    )


def _read__build_stats(
    *,
    mode: str,
    batch_size: int,
    total_count: int | None,
    total_pages: int | None,
    current_page: int | None,
    has_next: bool,
    has_previous: bool,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "batch_size": batch_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": current_page,
        "has_next": has_next,
        "has_previous": has_previous,
    }


def _update__prepare_frames(
    model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
):
    """Canonicalize primary keys and record what each frame leaves out.

    Returns ``(frames, unset_columns)``. ``unset_columns[model]`` is the set of
    db column names the frame does not carry, primary key excluded. Those columns
    stay absent for the rest of the pipeline and never reach the staging table,
    so the merge omits them from both the INSERT list and the ``SET`` clause and
    the database keeps whatever an existing row already holds.

    Nothing is read back from the database here — there is no query to route, so
    an explicit connection that was never registered for ORM use stays usable for
    the write itself.
    """
    updated_model_frames = {}
    unset_columns = {}
    for model_cls, frm in model_frms.items():
        # Resolve the schema once (lazy-safe); the canonicalization below only
        # touches values, so column membership stays valid for the iteration.
        df_cols = set(mo_polars_kit.resolve_schema(frm).names())
        pk_name = model_cls._meta.pk.name
        pk_column = model_cls._meta.pk.column
        pk_field = next((c for c in (pk_column, pk_name) if c in df_cols), None)
        mo_validation_kit.ensure_truthy(
            pk_field,
            msg=f"Primary key {pk_field} must exist in DataFrame to match rows for update.",
            is_exception=True,
        )
        model_fields = {f.column or f.name for f in model_cls._meta.concrete_fields}
        # The primary key is never "unset": it is how rows are matched, and it is
        # present by the check above under whichever of its two names.
        unset_columns[model_cls] = {
            col for col in model_fields - df_cols if col != pk_field
        }
        # Canonicalize UUID primary keys to the stored dashless/lowercase form so
        # upsert matching lands on the right row regardless of the input form
        # (e.g. hyphenated frames from `read()`) or backend (SQLite stores UUIDs
        # dashless, PostgreSQL hyphenated). The expr casts to Utf8.
        if isinstance(model_cls._meta.pk, models.UUIDField):
            frm = frm.with_columns(_canonical_uuid_expr(pk_field))
        updated_model_frames[model_cls] = frm
    return updated_model_frames, unset_columns


# ----------------
# Entry Point
# ----------------
mo_crud_kit = MindoffCRUDHandler()
