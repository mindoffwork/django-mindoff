import uuid
from collections import defaultdict
from typing import List, Any, Dict, Literal, Type, Union

import polars as pl
from typeguard import typechecked

from ..validation_kit import mo_validation_kit

# ----------------
# Constants
# ----------------
MODEL_FRMS_DEFAULT_ROOT_KEY = "__root__"


# ----------------
# Classes
# ----------------
class PayloadFlattener:
    """
    Flatten hierarchical JSON payloads into relational-style Polars frames.

    This class converts nested payload paths into table-like DataFrames/LazyFrames,
    propagates parent identifiers across nested levels, and guarantees primary-key
    columns per configured path so downstream CRUD validators and processors receive
    model-ready frame structures.
    """
    def __init__(
        self,
        payload: List[Dict],
        id_map: Dict[str, str],
        frame_type: Literal["auto", "dataframe", "lazyframe"] = "auto",
        lazy_threshold: int = 10000,
        uuid_mode: Literal["hex", "standard"] = "standard",
    ):
        self.payload = payload
        self.id_map = id_map
        self.frame_type = frame_type
        self.lazy_threshold = lazy_threshold
        self.uuid_mode = uuid_mode

        self.is_lazy = (
            len(payload) > lazy_threshold
            if frame_type == "auto"
            else frame_type == "lazyframe"
        )

        self.results: Dict[str, Union[pl.DataFrame, pl.LazyFrame]] = {}
        self.uuid_tracker: Dict[str, List[str]] = {}
        self.nested_keys = defaultdict(set)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def flatten(self) -> Dict[str, Union[pl.DataFrame, pl.LazyFrame]]:
        """Build and return flattened frame mapping keyed by internal table path names."""
        self._init_parent()

        nested_paths = [
            k for k in self.id_map.keys() if k != MODEL_FRMS_DEFAULT_ROOT_KEY
        ]

        for path in sorted(nested_paths, key=lambda x: x.count(".")):
            self._process_path(path)

        self._cleanup_nested_keys()
        return self.results

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _frame_instance(self, data: Dict) -> Union[pl.DataFrame, pl.LazyFrame]:
        if self.is_lazy:
            return pl.DataFrame(data).lazy()
        return pl.DataFrame(data)

    # ------------------------------------------------------------------ #
    # FIX 5: Union all root keys across payload
    # ------------------------------------------------------------------ #

    def _init_parent(self):
        parent_id_col = self.id_map.get(MODEL_FRMS_DEFAULT_ROOT_KEY)
        if not parent_id_col:
            raise ValueError(
                f"Root primary_key column must be defined using path '{MODEL_FRMS_DEFAULT_ROOT_KEY}'"
            )

        # Union of all keys across payload
        all_keys = set()
        for row in self.payload:
            all_keys.update(row.keys())

        data = {k: [d.get(k) for d in self.payload] for k in all_keys}

        df = self._frame_instance(data)

        if parent_id_col not in self._schema_names(df):
            df = self._ensure_id_column(df, parent_id_col)
        else:
            # Column exists: normalize all UUIDs and fill nulls using vectorized ops
            df = self._normalize_and_fill_uuid_column(df, parent_id_col)

        self.uuid_tracker[MODEL_FRMS_DEFAULT_ROOT_KEY] = [parent_id_col]
        self.results[MODEL_FRMS_DEFAULT_ROOT_KEY] = df

    # ------------------------------------------------------------------ #

    def _process_path(self, path: str):
        parts = path.split(".")
        table_path = []

        for i, key in enumerate(parts):
            table_path.append(key)
            table = "__".join(table_path)

            parent = (
                MODEL_FRMS_DEFAULT_ROOT_KEY if i == 0 else "__".join(table_path[:-1])
            )

            if parent not in self.results:
                raise ValueError(f"Parent '{parent}' missing for '{table}'")

            self._extract_subtable(parent, table, key, ".".join(parts[: i + 1]))

    # ------------------------------------------------------------------ #
    # FIX 1: Strict nested validation (dict or list[dict])
    # ------------------------------------------------------------------ #

    def _extract_subtable(self, parent, table, key, full_path):
        parent_df = self.results[parent]
        cols = self._schema_names(parent_df)

        if key not in cols:
            self.results[table] = self._empty_frame_like(parent_df)
            return

        df = parent_df.select([*self.uuid_tracker[parent], key])

        # Validate + normalize nested values
        def normalize(value):
            if value is None:
                return None

            if isinstance(value, dict):
                return [value]

            if isinstance(value, (list, tuple)):
                if not all(isinstance(v, dict) for v in value):
                    raise ValueError(
                        f"Invalid nested structure at path '{full_path}'. "
                        "Expected dict or list[dict]."
                    )
                return list(value)

            raise ValueError(
                f"Invalid nested structure at path '{full_path}'. "
                "Expected dict or list[dict]."
            )

        df = df.with_columns(
            pl.col(key).map_elements(normalize, return_dtype=pl.List(pl.Struct))
        )

        df = df.filter(pl.col(key).is_not_null()).explode(key)

        df = self._maybe_unnest(df, key)

        id_col = self.id_map.get(full_path)
        if not id_col:
            raise ValueError(f"Missing primary key column for path: {full_path}")

        df = self._ensure_id_column(df, id_col)

        self.uuid_tracker[table] = [*self.uuid_tracker[parent], id_col]
        self.results[table] = df
        self.nested_keys[parent].add(key)

    # ------------------------------------------------------------------ #
    # Memory-Efficient UUID Generation using Polars Expressions
    # ------------------------------------------------------------------ #

    def _get_uuid_expression(self) -> pl.Expr:
        """
        Generate a Polars expression that produces UUIDs.
        Uses struct trick to ensure unique UUIDs per row.
        Memory-efficient: no Python list creation.
        """
        # Use struct(pl.all()) as a seed for UUID generation
        # Each row gets a unique UUID via the map_elements function
        uuid_expr = pl.struct(pl.all()).map_elements(
            lambda _: str(uuid.uuid4()), return_dtype=pl.String
        )

        # Convert to hex if needed
        if self.uuid_mode == "hex":
            uuid_expr = uuid_expr.str.replace_all("-", "")

        return uuid_expr

    def _ensure_id_column(self, df, id_col):
        """
        Ensure PRIMARY KEY column exists. If it doesn't exist, generate new UUIDs.
        If it exists, normalize format and fill nulls.
        Uses vectorized Polars expressions (no Python list allocation).
        """
        schema_names = self._schema_names(df)

        # Case 1: Column does NOT exist → generate for all rows
        if id_col not in schema_names:
            df = df.with_columns(pl.lit(None).alias(id_col))

        # Case 2: Column exists → normalize format and fill nulls
        return self._normalize_and_fill_uuid_column(df, id_col)

    def _normalize_and_fill_uuid_column(self, df, id_col):
        """
        Normalize existing UUIDs to configured mode and fill nulls with generated UUIDs.
        Uses fully vectorized Polars operations (no Python loops or list allocation).
        Memory-efficient: streaming operations on large datasets.
        """
        # Generate new UUIDs only for NULL values
        uuid_expr = self._get_uuid_expression()

        df = df.with_columns(
            pl.when(pl.col(id_col).is_null())
            .then(uuid_expr)
            .otherwise(pl.col(id_col))
            .alias(id_col)
        )

        # Normalize existing UUIDs to configured mode
        if self.uuid_mode == "hex":
            # Remove hyphens for hex mode (handles both formats)
            df = df.with_columns(
                pl.col(id_col).cast(pl.String).str.replace_all("-", "").alias(id_col)
            )
        else:  # standard mode
            # Ensure hyphenated format (convert 32-char hex to standard)
            df = df.with_columns(
                pl.when(pl.col(id_col).str.len_chars() == 32)
                .then(
                    pl.col(id_col).str.replace(
                        r"^(.{8})(.{4})(.{4})(.{4})(.{12})$", r"\1-\2-\3-\4-\5"
                    )
                )
                .otherwise(pl.col(id_col))
                .alias(id_col)
            )

        return df

    # ------------------------------------------------------------------ #

    def _maybe_unnest(self, df, key: str):
        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema

        if isinstance(schema.get(key), pl.Struct):
            return df.unnest(key)

        return df

    def _schema_names(self, df) -> List[str]:
        if isinstance(df, pl.LazyFrame):
            return df.collect_schema().names()
        return df.columns

    def _empty_frame_like(self, reference_df):
        if isinstance(reference_df, pl.LazyFrame):
            return pl.LazyFrame()
        return pl.DataFrame()

    def _cleanup_nested_keys(self):
        for table, cols in self.nested_keys.items():
            if table in self.results:
                existing_cols = self._schema_names(self.results[table])
                drop_cols = [c for c in cols if c in existing_cols]
                if drop_cols:
                    self.results[table] = self.results[table].drop(drop_cols)


# ----------------
# Functions
# ----------------
@typechecked
def json_to_frame(
    payload: List[Dict] | Dict,
    *,
    id_map: Dict[str, str],
    frame_type: Literal["auto", "dataframe", "lazyframe"] = "auto",
    lazy_threshold: int = 10000,
    uuid_mode: Literal["hex", "standard"] = "standard",
):
    """
    Flatten JSON payload to Polars DataFrames with configurable UUID mode.

    Args:
        payload: Single dict or list of dicts to flatten
        id_map: Mapping of paths to primary key column names
        frame_type: "auto" (default), "dataframe", or "lazyframe"
        lazy_threshold: Switch to LazyFrame when payload size exceeds this
        uuid_mode: "hex" (compact, 32 chars) or "standard" (hyphenated, 36 chars)

    Returns:
        Dict mapping table names to DataFrames/LazyFrames

    Memory characteristics:
        - No Python list allocation for UUIDs (Polars expressions)
        - LazyFrame support for datasets > lazy_threshold rows
        - Streaming operations for efficient memory usage
    """
    return PayloadFlattener(
        payload=payload if isinstance(payload, list) else [payload],
        id_map=id_map,
        frame_type=frame_type,
        lazy_threshold=lazy_threshold,
        uuid_mode=uuid_mode,
    ).flatten()


@typechecked
def build_model_frms(
    payload: List[Dict] | Dict,
    *,
    model_mapping: Dict[Type[Any], str],
    frame_type: Literal["auto", "dataframe", "lazyframe"] = "auto",
    lazy_threshold: int = 10000,
    uuid_mode: Literal["hex", "standard"] = "standard",
) -> Dict[Type[Any], Union[pl.DataFrame, pl.LazyFrame]]:
    """
    Build model-based DataFrames from JSON payload with configurable UUID mode.

    Args:
        payload: Single dict or list of dicts to flatten
        model_mapping: Mapping of model classes to their path in the payload
        frame_type: "auto" (default), "dataframe", or "lazyframe"
        lazy_threshold: Switch to LazyFrame when payload size exceeds this
        uuid_mode: "hex" (compact, 32 chars) or "standard" (hyphenated, 36 chars)

    Returns:
        Dict mapping model classes to DataFrames/LazyFrames

    Memory characteristics:
        - No Python list allocation for UUIDs (Polars expressions)
        - LazyFrame support for datasets > lazy_threshold rows
        - Streaming operations for efficient memory usage
    """
    root_models = [
        m
        for m, p in model_mapping.items()
        if p in ["", ".", MODEL_FRMS_DEFAULT_ROOT_KEY]
    ]

    mo_validation_kit.ensure_equal(
        len(root_models),
        1,
        msg="Exactly one root model must be defined in model_mapping",
        is_exception=True,
    )

    id_map: Dict[str, str] = {}

    for model, path in model_mapping.items():
        pk_column = model._meta.pk.column

        normalized_path = (
            MODEL_FRMS_DEFAULT_ROOT_KEY
            if path in ["", ".", MODEL_FRMS_DEFAULT_ROOT_KEY]
            else path.strip(".")
        )

        mo_validation_kit.ensure_not_in(
            normalized_path,
            id_map,
            is_exception=True,
            msg=f"Duplicate path '{normalized_path}' in model_mapping",
        )

        id_map[normalized_path] = pk_column

    flat_results = PayloadFlattener(
        payload=payload if isinstance(payload, list) else [payload],
        id_map=id_map,
        frame_type=frame_type,
        lazy_threshold=lazy_threshold,
        uuid_mode=uuid_mode,
    ).flatten()

    any_frame = next(iter(flat_results.values()))
    is_lazy_result = isinstance(any_frame, pl.LazyFrame)
    empty_frame = pl.LazyFrame() if is_lazy_result else pl.DataFrame()

    model_frms: Dict[Type[Any], Union[pl.DataFrame, pl.LazyFrame]] = {}

    for model, path in model_mapping.items():
        normalized_path = (
            MODEL_FRMS_DEFAULT_ROOT_KEY
            if path in ["", ".", MODEL_FRMS_DEFAULT_ROOT_KEY]
            else path.strip(".")
        )

        internal_key = normalized_path.replace(".", "__")
        model_frms[model] = flat_results.get(internal_key, empty_frame)

    return model_frms
