from functools import partial
from types import SimpleNamespace
from typing import List, Any, Callable, Dict, Literal, Tuple, Type, Union

import polars as pl
import pathlib
import uuid
import tempfile
from typeguard import typechecked

from .validation_kit import mo_validation_kit
from ._polars_kit.json_to_frame import json_to_frame, build_model_frms
from pathlib import Path


# ----------------
# Classes
# ----------------
class MindoffPolarsKit:

    # Model Frame Handling
    @typechecked
    def is_frm_empty(self, frm: pl.DataFrame | pl.LazyFrame) -> bool:
        if isinstance(frm, pl.DataFrame):
            return frm.is_empty()
        if not frm.collect_schema():
            return True
        return frm.limit(1).collect().is_empty()

    @typechecked
    def is_model_frms_empty(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> bool:
        return all(self.is_frm_empty(frm) for frm in model_frms.values())

    @typechecked
    def is_model_frms_not_empty(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> bool:
        return any(not self.is_frm_empty(frm) for frm in model_frms.values())

    @typechecked
    def collect_model_frms(
        self,
        df_dict: Dict[Any, Union[pl.DataFrame, pl.LazyFrame]],
        streaming: bool = True,
    ) -> Dict[Any, pl.DataFrame]:
        collected_model_frms = {}
        for model, frm in df_dict.items():
            if isinstance(frm, pl.LazyFrame):
                collected_model_frms[model] = frm.collect(streaming=streaming)
            elif isinstance(frm, pl.DataFrame):
                collected_model_frms[model] = frm
        return collected_model_frms

    @typechecked
    def sync_model_frms_type(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> dict[Any, pl.DataFrame | pl.LazyFrame]:
        any_lazy = any(isinstance(v, pl.LazyFrame) for v in model_frms.values())
        if not any_lazy:
            return model_frms
        synced_model_frms = {}
        for k, v in model_frms.items():
            if isinstance(v, pl.DataFrame):
                synced_model_frms[k] = v.lazy()
            else:
                synced_model_frms[k] = v
        return synced_model_frms

    # Regular Frame Handling
    @typechecked
    def has_nulls_in_frm_col(
        self, frm: pl.DataFrame | pl.LazyFrame, column: str
    ) -> bool:
        if isinstance(frm, pl.DataFrame):
            return frm[column].null_count() > 0
        return frm.select(pl.col(column).is_null().any()).collect().item()

    @typechecked
    def split_model_frms_on_null(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
        column: str = "__error__info",
    ) -> tuple[
        dict[Any, pl.DataFrame | pl.LazyFrame],
        dict[Any, pl.DataFrame | pl.LazyFrame],
    ]:
        valid_dfs, invalid_dfs = {}, {}
        for model, frm in model_frms.items():
            schema = frm.schema
            work_df = frm
            if column not in schema:
                work_df = frm.with_columns(pl.lit(None, dtype=pl.String).alias(column))
            valid_dfs[model] = work_df.filter(pl.col(column).is_null())
            invalid_dfs[model] = work_df.filter(pl.col(column).is_not_null())
        return valid_dfs, invalid_dfs

    @typechecked
    def frm_fill_null(
        self,
        fr: pl.DataFrame | pl.LazyFrame,
        *,
        column: str,
        fill_value: Any | Callable[..., Any],
        mode: Literal["lit", "map", "sink_map"],
        dtype: type | None = None,
        **custom_params: Any,
    ) -> pl.DataFrame | pl.LazyFrame:
        if mode == "lit":
            value = fill_value(**custom_params) if callable(fill_value) else fill_value
            return fr.with_columns(
                pl.col(column).fill_null(pl.lit(value, dtype=dtype)).alias(column)
            )
        mo_validation_kit.ensure_truthy(
            callable(fill_value),
            msg=f"fill_value must be callable when mode='{mode}'",
            is_exception=True,
        )
        loaded_func = partial(fill_value, **custom_params)

        def _logic(s: pl.Series, func, target_dtype):
            if s.null_count() == 0:
                return s
            data = s.to_list()
            for i, v in enumerate(data):
                if v is None:
                    data[i] = func()
            return pl.Series(s.name, data, dtype=target_dtype)

        return _apply_batch_transform(
            fr,
            column=column,
            mode=mode,
            dtype=dtype,
            batch_logic=_logic,
            loaded_func=loaded_func,
        )

    @typechecked
    def frm_fill_notnull(
        self,
        fr: pl.DataFrame | pl.LazyFrame,
        *,
        column: str,
        fill_value: Any | Callable[..., Any],
        mode: Literal["lit", "map", "sink_map"],
        row_param: str | None = None,
        dtype: type | None = None,
        **custom_params: Any,
    ) -> pl.DataFrame | pl.LazyFrame:
        mo_validation_kit.ensure(
            lambda: not row_param or mode in ["map", "sink_map"],
            msg="'row_param' can only be used with mode='map' or 'sink_map'",
            is_exception=True,
        )
        if mode == "lit":
            value = fill_value(**custom_params) if callable(fill_value) else fill_value
            return fr.with_columns(
                pl.when(pl.col(column).is_not_null())
                .then(pl.lit(value, dtype=dtype))
                .otherwise(pl.col(column))
                .alias(column)
            )
        mo_validation_kit.ensure_truthy(
            callable(fill_value),
            msg=f"fill_value must be callable when mode='{mode}'",
            is_exception=True,
        )
        loaded_func = partial(fill_value, **custom_params)

        def _logic(s: pl.Series, func, target_dtype):
            if s.null_count() == s.len():
                return s
            data = s.to_list()
            for i, v in enumerate(data):
                if v is not None:
                    data[i] = func(**{row_param: v}) if row_param else func()
            return pl.Series(s.name, data, dtype=target_dtype)

        return _apply_batch_transform(
            fr,
            column=column,
            mode=mode,
            dtype=dtype,
            batch_logic=_logic,
            loaded_func=loaded_func,
        )

    @typechecked
    def get_frm_height(self, frm: pl.DataFrame | pl.LazyFrame) -> int:
        if isinstance(frm, pl.DataFrame):
            return frm.height
        result = frm.select(pl.len()).collect(engine="streaming")
        height = result.item()
        del result
        return height


# ----------------
# Helper Functions
# ----------------
def _apply_batch_transform(
    fr: pl.DataFrame | pl.LazyFrame,
    *,
    column: str,
    mode: Literal["map", "sink_map"],
    dtype: type | None,
    batch_logic: Callable[[pl.Series, Callable[..., Any], type], pl.Series],
    loaded_func: Callable[..., Any],
) -> pl.DataFrame | pl.LazyFrame:
    target_dtype = dtype or fr.schema.get(column) or pl.String

    def _batch_wrapper(s: pl.Series) -> pl.Series:
        return batch_logic(s, loaded_func, target_dtype)

    transformed_fr = fr.with_columns(
        pl.col(column)
        .map_batches(_batch_wrapper, return_dtype=target_dtype)
        .alias(column)
    )
    if mode == "map" or isinstance(fr, pl.DataFrame):
        return transformed_fr
    tmp_dir = Path(tempfile.gettempdir()) / "django_mindoff" / "polars_processing"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"freeze_{uuid.uuid4().hex}.parquet"
    transformed_fr.sink_parquet(str(tmp_path))
    return pl.scan_parquet(str(tmp_path))


# ----------------
# Entry Point
# ----------------
mo_polars_kit = MindoffPolarsKit()
