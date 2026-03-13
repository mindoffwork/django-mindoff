"""
Mindoff CRUD Kit
1. mo_crud_kit.create
2. mo_crud_kit.update
3. mo_crud_kit.read
"""

import warnings
from itertools import islice
from typing import Any, Dict, List, Tuple, Type, Union
import polars as pl
from typeguard import typechecked
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db import models
from django.db.models import F
from django.db.models import CharField
from django.db.models.functions import Cast
from ._crud_kit.column_validator import ColumnValidator
from ._crud_kit.crud_processor import CRUDProcessor
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
        is_partial: bool = False,
        is_validate: bool = True,
        batch_size: int = 1000,
    ) -> Tuple[str, Dict, Dict]:
        """Create rows from model-to-frame mappings with optional validation pipeline.

        Usage:

        ```python
        from django_mindoff.components.crud_kit import mo_crud_kit

        status, valid_model_frms, invalid_model_frms = mo_crud_kit.create(
            {
                OrderModel: order_df,
                OrderItemModel: order_item_df,
            },
            is_partial=False,
            is_validate=True,
            batch_size=1000,
        )
        ```

        Parameters:

        - `model_frms` (`dict[type[models.Model], pl.DataFrame|pl.LazyFrame]`):
          Input model-frame mapping for bulk insert.
        - `is_partial` (`bool, default=False`):
          If `True`, allows partial save when only a subset of rows are valid.
        - `is_validate` (`bool, default=True`):
          If `True`, runs column, row, and foreign-key validation before insert.
        - `batch_size` (`int, default=1000`):
          Batch size used by the underlying write process.

        Varieties:

        - Validation mode:
          `is_validate=True` runs `ColumnValidator -> RowValidator -> ForeignKeyValidator`.
        - Partial-save mode:
          `is_partial=False` fails if any invalid rows exist;
          `is_partial=True` saves valid rows and returns invalid rows separately.

        Possible responses:

        - Returns `("ok", valid_model_frms, {})` when all rows are valid and inserted.
        - Returns `("partial_ok", valid_model_frms, invalid_model_frms)` when partial mode is enabled and some rows are invalid.
        - Returns `("fail", valid_model_frms, invalid_model_frms)` when validation fails and no write should proceed.

        Notes:

        - Invalid rows contain an error column (`POLARS_VALIDATOR_ERROR_COL` or `__error__info`).
        - `is_validate=False` skips safety checks and may persist unsafe data.
        """
        model_frms = mo_polars_kit.sync_model_frms_type(model_frms)
        if is_validate:
            # 1. Column validation
            column_validator = ColumnValidator(
                model_frms=model_frms,
                is_remove_extra_columns=True,
                is_add_missing_columns=True,
            )
            valid_model_frms, invalid_model_frms = column_validator.run()
            if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
                return "fail", valid_model_frms, invalid_model_frms

            # 2. Row validation
            row_validator = RowValidator(valid_model_frms)
            row_validated_frms = row_validator.run()

            # 3. Foreign key validation
            fk_validator = ForeignKeyValidator(row_validated_frms)
            fk_validated_frms = fk_validator.validate()
            frm_dict_valid_invalid_splitter = _ModelFrmsValidInvalidSplitter(
                fk_validated_frms
            )
            valid_model_frms, invalid_model_frms = frm_dict_valid_invalid_splitter.run()

            # 4. Decide partial save
            if mo_polars_kit.is_model_frms_empty(valid_model_frms):
                return "fail", valid_model_frms, invalid_model_frms
            if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
                if not is_partial:
                    return "fail", valid_model_frms, invalid_model_frms
                status = "partial_ok"
            else:
                status = "ok"
        else:
            warnings.warn(
                "Saving without validation may store unsafe or inconsistent data, "
                "which can affect further read/update/delete functions. "
                "Proceed only if intentional.",
                RuntimeWarning,
            )
            status = "ok"
            valid_model_frms, invalid_model_frms = model_frms, {}

        # 5. Perform CRUD operation
        crud_processor = CRUDProcessor(valid_model_frms)
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
    ) -> tuple[pl.DataFrame | pl.LazyFrame, dict[str, Any]]:
        """Read queryset data into Polars with streaming/pagination variants.

        Usage:

        ```python
        from django_mindoff.components.crud_kit import mo_crud_kit

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
        - `batch_size` (`int, default=0`):
          Chunk/page size. Auto-resolved when `0`.

        Varieties:

        - Streaming mode (`page_number=None`): reads full dataset in chunks.
        - Pagination mode (`page_number=<n>`): reads one page and returns paging metadata.
        - Materialization mode: eager (`DataFrame`) or lazy (`LazyFrame`).

        Possible responses:

        - Returns `(frm, stats)` where `frm` is `DataFrame`/`LazyFrame` and `stats` includes:
          `mode`, `batch_size`, `total_count`, `total_pages`, `current_page`, `has_next`, `has_previous`.
        - Returns empty frame with zeroed stats for empty querysets.
        - Raises validation error if queryset is not `.values()`-based.
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

        # 2. Empty Queryset
        if not qs.exists():
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
            frm = pl.concat(
                _read__batched_iterator(qs, batch_size, is_lazy), rechunk=False
            )
            stats = _read__build_stats(
                mode="streaming",
                batch_size=batch_size,
                total_count=qs.count(),
                total_pages=0,
                current_page=0,
                has_next=False,
                has_previous=False,
            )
            return frm, stats

        # 4. Pagination mode
        paginator = Paginator(qs, batch_size)
        try:
            page = paginator.page(page_number)
            frm = (
                pl.DataFrame(list(page)).lazy() if is_lazy else pl.DataFrame(list(page))
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

    @typechecked
    def update(
        self,
        model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
        *,
        is_partial: bool = False,
        is_validate: bool = True,
        batch_size: int = 1000,
        is_temp_table: bool = True,
    ):
        """Upsert rows from model-to-frame mappings with optional staged merge strategy.

        Usage:

        ```python
        from django_mindoff.components.crud_kit import mo_crud_kit

        status, valid_model_frms, invalid_model_frms = mo_crud_kit.update(
            {
                OrderModel: order_updates_df,
            },
            is_partial=True,
            is_validate=True,
            batch_size=1000,
            is_temp_table=True,
        )
        ```

        Parameters:

        - `model_frms` (`dict[type[models.Model], pl.DataFrame|pl.LazyFrame]`):
          Input model-frame mapping for bulk update/upsert.
        - `is_partial` (`bool, default=False`):
          If `True`, allows valid rows to proceed even when invalid rows exist.
        - `is_validate` (`bool, default=True`):
          If `True`, applies model-based column/row/FK validation before update.
        - `batch_size` (`int, default=1000`):
          Batch size used for missing-column fetch and update processing.
        - `is_temp_table` (`bool, default=True`):
          If `True`, writes to staging table then merges;
          if `False`, performs direct dialect-specific upsert.

        Varieties:

        - Validation mode:
          enabled (`is_validate=True`) or skipped (`is_validate=False`).
        - Partial mode:
          fail-fast (`is_partial=False`) or partial success (`is_partial=True`).
        - Upsert mode:
          staging merge (`is_temp_table=True`) or direct upsert (`is_temp_table=False`).

        Possible responses:

        - Returns `("ok", valid_model_frms, {})` when all rows are valid and updated.
        - Returns `("partial_ok", valid_model_frms, invalid_model_frms)` when partial mode is enabled and some rows are invalid.
        - Returns `("fail", valid_model_frms, invalid_model_frms)` when validation blocks update.

        Notes:

        - Missing DB columns are auto-fetched using primary key before validation.
        - Invalid rows include model-aware error details in error column.
        """
        model_frms = _update__fill_missing_columns(model_frms, batch_size=batch_size)
        if is_validate:
            # 1. Column validation
            column_validator = ColumnValidator(
                model_frms=model_frms,
                is_remove_extra_columns=True,
                is_add_missing_columns=True,
            )
            valid_model_frms, invalid_model_frms = column_validator.run()
            if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
                return "fail", valid_model_frms, invalid_model_frms

            # 2. Row validation
            row_validator = RowValidator(valid_model_frms)
            row_validated_frms = row_validator.run()

            # 3. Foreign key validation
            fk_validator = ForeignKeyValidator(row_validated_frms)
            fk_validated_frms = fk_validator.validate()
            frm_dict_valid_invalid_splitter = _ModelFrmsValidInvalidSplitter(
                fk_validated_frms
            )
            valid_model_frms, invalid_model_frms = frm_dict_valid_invalid_splitter.run()

            # 4. Decide partial save
            if mo_polars_kit.is_model_frms_empty(valid_model_frms):
                return "fail", valid_model_frms, invalid_model_frms
            if not mo_polars_kit.is_model_frms_empty(invalid_model_frms):
                if not is_partial:
                    return "fail", valid_model_frms, invalid_model_frms
                status = "partial_ok"
            else:
                status = "ok"
        else:
            warnings.warn(
                "Updating without validation may store unsafe or inconsistent data, "
                "which can affect mindoff's read/update/delete functions. "
                "Proceed only if intentional.",
                RuntimeWarning,
            )
            status = "ok"
            valid_model_frms, invalid_model_frms = model_frms, {}

        # 5. Perform CRUD operation
        crud_processor = CRUDProcessor(valid_model_frms)
        _ = crud_processor.update(is_temp_table=is_temp_table, batch_size=batch_size)
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
        relations = []
        for model in models_list:
            for f in model._meta.concrete_fields:
                if isinstance(f, (models.ForeignKey, models.OneToOneField)):
                    relations.append(
                        (
                            model,
                            f.db_column or f.name,
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
def _read__batched_iterator(qs: models.QuerySet, size: int, is_lazy: bool):
    it = qs.iterator(chunk_size=size)
    while True:
        batch = list(islice(it, size))
        if not batch:
            break
        yield pl.DataFrame(batch).lazy() if is_lazy else pl.DataFrame(batch)


def _read__build_stats(
    *,
    mode: str,
    batch_size: int,
    total_count: int,
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


def _update__fill_missing_columns(
    model_frms: Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]],
    *,
    batch_size: int,
) -> Dict[Type[models.Model], Union[pl.DataFrame, pl.LazyFrame]]:
    updated_model_frames = {}
    for model_cls, frm in model_frms.items():
        df_cols = set(frm.columns)
        model_fields = {f.column or f.name for f in model_cls._meta.concrete_fields}
        missing_cols = list(model_fields - df_cols)
        pk_name = model_cls._meta.pk.name
        pk_column = model_cls._meta.pk.column
        pk_field = next((c for c in (pk_column, pk_name) if c in df_cols), None)
        mo_validation_kit.ensure_truthy(
            pk_field,
            msg=f"Primary key {pk_field} must exist in DataFrame to fetch missing columns.",
            is_exception=True,
        )
        missing_cols = [c for c in missing_cols if c != pk_field]
        if not missing_cols:
            updated_model_frames[model_cls] = frm
            continue
        schema = {pk_field: frm.schema[pk_field], **dict.fromkeys(missing_cols, None)}
        base_missing_df = (
            pl.LazyFrame(schema=schema)
            if isinstance(frm, pl.LazyFrame)
            else pl.DataFrame(schema=schema)
        )
        missing_df = __update__fetch_missing_chunks(
            model_cls, frm, pk_field, missing_cols, batch_size, base_missing_df
        )
        if mo_polars_kit.is_frm_empty(missing_df):
            for col in missing_cols:
                frm = frm.with_columns(pl.lit(None).alias(col))
            updated_model_frames[model_cls] = frm
            continue
        overlap = set(frm.columns) & set(missing_df.columns) - {pk_field}
        mo_validation_kit.ensure_falsey(
            overlap,
            msg=f"Duplicate columns found during merge for model {model_cls.__name__}: {', '.join(overlap)}",
            is_exception=True,
        )
        left_dtype = frm.schema[pk_field]
        right_dtype = missing_df.schema.get(pk_field)
        missing_df = (
            missing_df.with_columns(pl.col(pk_field).cast(left_dtype))
            if right_dtype is not None and left_dtype != right_dtype
            else missing_df
        )
        frm = frm.join(missing_df, on=pk_field, how="left")
        updated_model_frames[model_cls] = frm
    return updated_model_frames


def __update__fetch_missing_chunks(
    model_cls,
    frm,
    pk_field: str,
    missing_cols: list[str],
    batch_size: int,
    base_missing_df,
):
    is_lazy = isinstance(frm, pl.LazyFrame)
    if is_lazy:
        pk_series = frm.select(pk_field).collect(streaming=True)[pk_field]
    else:
        pk_series = frm.get_column(pk_field)
    if pk_series.is_empty():
        return base_missing_df
    pk_list = pk_series.to_list()
    total_rows = len(pk_list)
    collected_chunks = []
    temp_pk = "__pk_cast"
    if not mo_polars_kit.is_frm_empty(base_missing_df):
        collected_chunks.append(base_missing_df)
    for i in range(0, total_rows, batch_size):
        pk_chunk = pk_list[i : i + batch_size]
        qs = (
            model_cls.objects.filter(**{f"{pk_field}__in": pk_chunk})
            .annotate(**{temp_pk: Cast(F(pk_field), output_field=CharField())})
            .values(temp_pk, *missing_cols)
        )
        chunk_df, _ = MindoffCRUDHandler().read(
            qs,
            is_lazy=is_lazy,
            batch_size=batch_size,
        )
        if mo_polars_kit.is_frm_empty(chunk_df):
            continue
        chunk_df = chunk_df.rename({temp_pk: pk_field})
        collected_chunks.append(chunk_df)
    if not collected_chunks:
        return base_missing_df
    return pl.concat(collected_chunks, rechunk=False)


# ----------------
# Entry Point
# ----------------
mo_crud_kit = MindoffCRUDHandler()
