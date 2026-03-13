# Polars Kit

The Polars Kit is the frame-processing utility layer of `django-mindoff`. It provides shared DataFrame/LazyFrame helpers used by CRUD, validation, and payload-flattening workflows.

Its purpose is to keep frame operations predictable across eager/lazy execution modes while preserving model-aware data semantics.

For usage-focused examples, see [Developer Guide - Polars Utilities](../developer_guide/polars-utilities.md).

<div class="admonition warning">
<p class="admonition-title">Stability Warning</p>
<p><code>mo_polars_kit</code> is not part of the core offering and is currently experimental and subject to change without any prior notice. Refer to this page to keep informed on development with Polars utilities.</p>
</div>

## Architecture & Intent

Polars Kit has two runtime surfaces:

1. `mo_polars_kit` (`MindoffPolarsKit`) for frame utilities.
2. `_polars_kit/json_to_frame.py` for JSON-to-frame flattening and model-frame construction.

Together they support ingestion (`json_to_frame`), normalization (`sync_model_frms_type`), transformation (`frm_fill_*`), and operational checks (`is_frm_empty`, `get_frm_height`).

## Core Runtime Components

| Component | Responsibility | Examples |
| --- | --- | --- |
| **`MindoffPolarsKit`** | Public utility surface for emptiness checks, frame normalization, null transforms, and row counts. | `is_frm_empty`, `frm_fill_null` |
| **Batch transform engine** | Shared map/sink-map execution path for column mutation. | `_apply_batch_transform` |
| **Payload flattener** | Converts nested JSON payloads into table-like Polars frames. | `PayloadFlattener.flatten()` |
| **Model-frame builder** | Maps flattened path outputs to Django model classes. | `build_model_frms` |

## Frame Utility Architecture (`mo_polars_kit`)

### Emptiness & Height Primitives

- `is_frm_empty`: handles both `DataFrame` and `LazyFrame`, including schema-only lazy frames.
- `is_model_frms_empty` / `is_model_frms_not_empty`: map-wide emptiness predicates.
- `get_frm_height`: row count for eager and lazy frames using streaming collect for lazy.

These methods are used as control-flow gates in CRUD and validation pipelines.

### Model-Frame Normalization

- `collect_model_frms`: materializes all lazy values in a model-frame mapping.
- `sync_model_frms_type`: if any frame is lazy, converts all eager frames to lazy.

This enforces a single execution mode per operation and avoids mixed eager/lazy surprises.

### Null-State Splitting

- `has_nulls_in_frm_col`: null detection per column.
- `split_model_frms_on_null`: splits frames into valid/invalid partitions using an error marker column (default `__error__info`).

If the split column is absent, it is added as null and rows route to the valid partition.

### Column Mutation Pipeline

`frm_fill_null` and `frm_fill_notnull` provide three strategies:

1. `lit`: literal replacement (or callable evaluated once for literal mode).
2. `map`: batch map transformation in memory.
3. `sink_map`: lazy sink-to-parquet and re-scan path for large lazy pipelines.

Shared rules:

- `map`/`sink_map` require callable `fill_value`.
- `frm_fill_notnull` supports `row_param` only with `map`/`sink_map`.
- dtype can be explicitly controlled; otherwise inferred from schema.

## JSON Flattening Architecture (`json_to_frame`)

`PayloadFlattener` transforms nested payloads into table-style frames keyed by path.

### Flatten Lifecycle

1. Initialize root frame from payload union-of-keys.
2. Ensure/normalize root PK column using `id_map["__root__"]`.
3. Process nested paths in depth order.
4. Extract subtables via explode/unnest semantics.
5. Ensure each path-specific primary key column exists and is normalized.
6. Drop nested object/list columns from parent tables after extraction.

### Structural Rules

- Root PK mapping is mandatory.
- Nested values must be `dict` or `list[dict]` (or null).
- Missing nested key yields empty subtable frame.
- Missing path PK mapping raises error.

### UUID Normalization

Supports `uuid_mode`:

- `hex`: 32-char compact UUID.
- `standard`: hyphenated UUID format.

Existing IDs are normalized; missing IDs are generated via vectorized Polars expressions.

### Frame Type Strategy

- `frame_type="dataframe"`: eager output.
- `frame_type="lazyframe"`: lazy output.
- `frame_type="auto"`: lazy when payload size exceeds `lazy_threshold`.

## Model Mapping Architecture (`build_model_frms`)

`build_model_frms` maps flattened path tables back to model classes.

Key constraints:

- Exactly one root model path is required (`""`, `"."`, or `__root__`).
- Duplicate normalized paths are rejected.
- Model PK db columns are used as ID columns in generated `id_map`.

Outputs preserve eager/lazy type consistency from flattening.

## Integration With Other Runtime Layers

- **CRUD Kit:** uses emptiness checks, type sync, height checks, and null transforms.
- **Row validation:** uses fill helpers for default/value normalization.
- **Payload ingestion workflows:** use JSON flattening for model-aware frame generation.
- **Validation Kit:** guards mode/callable constraints inside mutation helpers.

## Operational Caveats

- `sink_map` for lazy frames writes temporary parquet files under temp storage and returns `scan_parquet` lazy frames.
- `is_model_frms_empty({})` returns `True` and `is_model_frms_not_empty({})` returns `False` by Python `all/any` semantics.
- Split-on-null adds missing marker column, which can affect downstream schema expectations if not anticipated.
- JSON flattening assumes declared `id_map` path coverage; undeclared nested PK paths fail fast.

## Troubleshooting the Kit

1. **Callable mode errors in fill helpers:** verify `fill_value` is callable for `map/sink_map`.
2. **Unexpected eager/lazy behavior:** check `sync_model_frms_type` and `frame_type`/`lazy_threshold` inputs.
3. **Flattening fails on nested payload:** ensure nested values are dict or list-of-dict and `id_map` contains path PK.
4. **Model frame mapping errors:** verify exactly one root model and no duplicate normalized paths.
5. **Temporary-file concerns with sink mode:** prefer `map` for smaller datasets or manage temp storage lifecycle.
