"""
mo_polars_kit
    all the functions inside MindoffPolarsKit
"""

import tempfile
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Union
import polars as pl
from .validation_kit import mo_validation_kit


# ----------------
# Classes
# ----------------
class MindoffPolarsKit:
    """Polars helper surface for model-frame workflows.

    Exposes utility methods used across CRUD/validation pipelines for:

    - frame emptiness checks
    - model-frame dictionary normalization
    - null/non-null transformation helpers
    - lazy/eager collection and row counting
    """

    def is_frm_empty(self, frm: pl.DataFrame | pl.LazyFrame) -> bool:
        """Check whether a Polars frame has zero rows.

        Usage:

        ```python
        is_empty = mo_polars_kit.is_frm_empty(frm)
        ```

        Parameters:

        - `frm` (`pl.DataFrame | pl.LazyFrame`): Frame to inspect.

        Possible responses:

        - Returns `True` when frame has no rows.
        - Returns `False` when at least one row exists.
        """
        if isinstance(frm, pl.DataFrame):
            return frm.is_empty()
        if not frm.collect_schema():
            return True
        return frm.limit(1).collect().is_empty()

    def is_model_frms_empty(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> bool:
        """Check whether every frame in a model-frame map is empty.

        Usage:

        ```python
        all_empty = mo_polars_kit.is_model_frms_empty(model_frms)
        ```

        Parameters:

        - `model_frms` (`dict[Any, pl.DataFrame|pl.LazyFrame]`): Model-frame mapping.

        Possible responses:

        - Returns `True` when all mapped frames are empty.
        - Returns `False` when any mapped frame contains rows.
        """
        return all(self.is_frm_empty(frm) for frm in model_frms.values())

    def is_model_frms_not_empty(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> bool:
        """Check whether at least one frame in a model-frame map is not empty.

        Usage:

        ```python
        has_data = mo_polars_kit.is_model_frms_not_empty(model_frms)
        ```

        Parameters:

        - `model_frms` (`dict[Any, pl.DataFrame|pl.LazyFrame]`): Model-frame mapping.

        Possible responses:

        - Returns `True` when at least one frame has rows.
        - Returns `False` when all frames are empty.
        """
        return any(not self.is_frm_empty(frm) for frm in model_frms.values())

    def collect_model_frms(
        self,
        df_dict: Dict[Any, Union[pl.DataFrame, pl.LazyFrame]],
        streaming: bool = True,
    ) -> Dict[Any, pl.DataFrame]:
        """Collect all lazy frames in a model-frame map into eager DataFrames.

        Usage:

        ```python
        collected = mo_polars_kit.collect_model_frms(model_frms, streaming=True)
        ```

        Parameters:

        - `df_dict` (`dict[Any, pl.DataFrame|pl.LazyFrame]`): Input model-frame mapping.
        - `streaming` (`bool, default=True`): Uses streaming collection for LazyFrame inputs.

        Possible responses:

        - Returns `dict[Any, pl.DataFrame]` with all values materialized as DataFrame.
        """
        collected_model_frms = {}
        for model, frm in df_dict.items():
            if isinstance(frm, pl.LazyFrame):
                collected_model_frms[model] = frm.collect(streaming=streaming)
            elif isinstance(frm, pl.DataFrame):
                collected_model_frms[model] = frm
        return collected_model_frms

    def sync_model_frms_type(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
    ) -> dict[Any, pl.DataFrame | pl.LazyFrame]:
        """Normalize model-frame map to a single Polars execution type.

        Usage:

        ```python
        synced = mo_polars_kit.sync_model_frms_type(model_frms)
        ```

        Parameters:

        - `model_frms` (`dict[Any, pl.DataFrame|pl.LazyFrame]`): Input mapping.

        Behavior:

        - If any value is `LazyFrame`, all `DataFrame` values are converted to `LazyFrame`.
        - If all values are `DataFrame`, mapping is returned unchanged.

        Possible responses:

        - Returns normalized model-frame mapping.
        """
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

    def has_nulls_in_frm_col(
        self, frm: pl.DataFrame | pl.LazyFrame, column: str
    ) -> bool:
        """Check whether a frame column contains null values.

        Usage:

        ```python
        has_nulls = mo_polars_kit.has_nulls_in_frm_col(frm, "email")
        ```

        Parameters:

        - `frm` (`pl.DataFrame | pl.LazyFrame`): Frame to inspect.
        - `column` (`str`): Target column name.

        Possible responses:

        - Returns `True` when at least one null is present.
        - Returns `False` when no nulls exist.
        """
        if isinstance(frm, pl.DataFrame):
            return frm[column].null_count() > 0
        return frm.select(pl.col(column).is_null().any()).collect().item()

    def split_model_frms_on_null(
        self,
        model_frms: dict[Any, pl.DataFrame | pl.LazyFrame],
        column: str = "__error__info",
    ) -> tuple[
        dict[Any, pl.DataFrame | pl.LazyFrame],
        dict[Any, pl.DataFrame | pl.LazyFrame],
    ]:
        """Split model-frame map into valid/invalid partitions by nullability of an error column.

        Usage:

        ```python
        valid, invalid = mo_polars_kit.split_model_frms_on_null(
            model_frms,
            column="__error__info",
        )
        ```

        Parameters:

        - `model_frms` (`dict[Any, pl.DataFrame|pl.LazyFrame]`): Model-frame mapping.
        - `column` (`str, default="__error__info"`): Error/status column used for split.

        Possible responses:

        - Returns `(valid_model_frms, invalid_model_frms)`.
        - Valid partition contains rows where `column` is null.
        - Invalid partition contains rows where `column` is not null.
        """
        valid_dfs, invalid_dfs = {}, {}
        for model, frm in model_frms.items():
            schema = self.resolve_schema(frm)
            work_df = frm
            if column not in schema:
                work_df = frm.with_columns(pl.lit(None, dtype=pl.String).alias(column))
            valid_dfs[model] = work_df.filter(pl.col(column).is_null())
            invalid_dfs[model] = work_df.filter(pl.col(column).is_not_null())
        return valid_dfs, invalid_dfs

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
        """Fill null values in a column using literal or callable modes.

        Usage:

        ```python
        frm = mo_polars_kit.frm_fill_null(
            frm,
            column="status",
            fill_value="draft",
            mode="lit",
        )
        ```

        Parameters:

        - `fr` (`pl.DataFrame | pl.LazyFrame`): Target frame.
        - `column` (`str`): Column to update.
        - `fill_value` (`Any | Callable`): Literal value or callable for generated values.
        - `mode` (`"lit" | "map" | "sink_map"`): Fill strategy.
        - `dtype` (`type | None, default=None`): Target dtype for generated values.
        - `**custom_params`: Additional kwargs passed to callable fill function.

        Varieties:

        - `lit`: direct literal fill.
        - `map`: in-memory batch mapping.
        - `sink_map`: lazy sink to parquet then scan back for large lazy pipelines.

        Possible responses:

        - Returns transformed `DataFrame`/`LazyFrame` with nulls filled.
        """
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
        """Transform non-null values in a column using literal or callable modes.

        Usage:

        ```python
        frm = mo_polars_kit.frm_fill_notnull(
            frm,
            column="email",
            fill_value=lambda row: row.lower(),
            mode="map",
            row_param="row",
            dtype=pl.Utf8,
        )
        ```

        Parameters:

        - `fr` (`pl.DataFrame | pl.LazyFrame`): Target frame.
        - `column` (`str`): Column to update.
        - `fill_value` (`Any | Callable`): Literal value or callable transform.
        - `mode` (`"lit" | "map" | "sink_map"`): Transformation strategy.
        - `row_param` (`str | None, default=None`): Callable kwarg name for current non-null value.
        - `dtype` (`type | None, default=None`): Target dtype.
        - `**custom_params`: Extra kwargs for callable transform.

        Varieties:

        - `lit`: replace all non-null values with one literal.
        - `map`: apply callable across batches.
        - `sink_map`: lazy sink-based map flow for large lazy frames.

        Possible responses:

        - Returns transformed `DataFrame`/`LazyFrame` with non-null values updated.
        """
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

    def resolve_schema(self, frm: pl.DataFrame | pl.LazyFrame) -> pl.Schema:
        """Return a frame's schema using the lazy-safe API.

        Usage:

        ```python
        schema = mo_polars_kit.resolve_schema(frm)
        names = schema.names()
        dtype = schema.get("email")
        ```

        Parameters:

        - `frm` (`pl.DataFrame | pl.LazyFrame`): Frame to inspect.

        Behavior:

        - For `LazyFrame`, uses `collect_schema()` — the explicit, warning-free
          schema resolution (plain `.schema`/`.columns` emit a Polars
          `PerformanceWarning`).
        - For `DataFrame`, returns `.schema` directly.

        Possible responses:

        - Returns a `pl.Schema` mapping column names to dtypes.
        """
        return frm.collect_schema() if isinstance(frm, pl.LazyFrame) else frm.schema

    def get_frm_height(self, frm: pl.DataFrame | pl.LazyFrame) -> int:
        """Get frame row count for eager or lazy Polars inputs.

        Usage:

        ```python
        total_rows = mo_polars_kit.get_frm_height(frm)
        ```

        Parameters:

        - `frm` (`pl.DataFrame | pl.LazyFrame`): Target frame.

        Possible responses:

        - Returns row count as `int`.
        """
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
    target_dtype = dtype or mo_polars_kit.resolve_schema(fr).get(column) or pl.String

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
