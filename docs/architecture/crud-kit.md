# CRUD Kit

The CRUD Kit is the data-engineering layer of `django-mindoff`. It runs model-aware bulk `create`, `read`, and `update` operations over Polars frames while enforcing Django model constraints.

It is designed for high-throughput tabular workflows where API-level validation and storage-level consistency both matter.

For usage-level CRUD implementation patterns, see [Developer Guide - Data Operations (CRUD)](../developer_guide/data-operations-crud.md).

## Architecture & Data Flow

At a high level, write operations pass through one shared validation pipeline before they reach database execution.

### Core Runtime Components

| Component | Responsibility | Examples |
| --- | --- | --- |
| **`MindoffCRUDHandler`** | Public API surface and lifecycle orchestration (`create`, `read`, `update`). | `mo_crud_kit.create(...)`, `mo_crud_kit.read(...)` |
| **Validators** | Normalize and validate structure, field values, and FK integrity. | `ColumnValidator`, `RowValidator`, `ForeignKeyValidator` |
| **`_ModelFrmsValidInvalidSplitter`** | Splits valid/invalid rows and propagates invalid dependency chains across related models. | Parent-child FK invalidation propagation |
| **`CRUDProcessor`** | Executes DB writes through SQLAlchemy/Polars with dialect-specific upsert behavior. | Append inserts, staging merge upserts |

## Validation Pipeline

`create(...)` and `update(...)` share the same ordered write pipeline when `is_validate=True`.

### 1. Column Contract Validation

`ColumnValidator` enforces model-frame alignment before any row-level work:

- Requires UUID primary keys.
- Requires explicit `db_column` on primary key and foreign keys.
- Normalizes field names to model `db_column` names.
- Handles missing/extra columns using configured behavior (CRUD defaults: add missing, remove extra).
- Fails fast on schema-contract errors.

### 2. Row Sanitization & Field Rules

`RowValidator` sanitizes and checks row values against Django field metadata:

- Text/slug/email/url normalization and type coercion.
- Numeric/date/time/duration/UUID/IP/JSON transformation.
- Default value application.
- Constraint checks (`required`, `max_length`, `max_digits`, min/max validators, dtype matching).
- Unsupported fields are explicitly rejected (for example `ManyToManyField`, `BinaryField`).

### 3. Foreign-Key Integrity

`ForeignKeyValidator` verifies FK references:

- Uses in-memory related frames when related models are present in the same operation.
- Falls back to database existence checks when related frames are absent.
- Rejects unresolved FK references as validation failures.

### 4. Relationship-Aware Invalid Propagation

`_ModelFrmsValidInvalidSplitter` does more than row splitting:

- Collects invalid IDs per model.
- Propagates invalidity upward and downward across FK edges.
- Produces `valid_model_frms` and `invalid_model_frms` that remain relationally consistent.

### 5. Partial vs Fail-Fast Behavior

After validation:

- Returns `fail` when no valid rows remain.
- Returns `fail` when invalid rows exist and `is_partial=False`.
- Returns `partial_ok` when invalid rows exist and `is_partial=True`.
- Returns `ok` when all rows are valid.

When `is_validate=False`, CRUD writes proceed directly and emit runtime warnings about unsafe persistence.

## Read Path Architecture

`read(...)` is intentionally strict on input and flexible on output.

### Input Contract

- Queryset must be `.values()`-based.
- `batch_size` must be `>= 0`.
- When `batch_size=0`, defaults are auto-selected to `1000` for streaming mode and `100` for pagination mode.

### Execution Modes

1. **Streaming mode** (`page_number=None`): iterates queryset in chunks and concatenates into one Polars frame.
2. **Pagination mode** (`page_number=<n>`): returns only one page plus paginator metadata.

Each mode supports eager (`pl.DataFrame`) and lazy (`pl.LazyFrame`) output.

### Read Response Metadata

`read(...)` returns `(frame, stats)` where `stats` includes:

- `mode`
- `batch_size`
- `total_count`
- `total_pages`
- `current_page`
- `has_next`
- `has_previous`

## Write Engine Architecture

`CRUDProcessor` handles database writes and update strategies.

### Engine & Dialect Resolution

- Builds a SQLAlchemy engine from Django `DATABASES`.
- Supports `sqlite`, `postgresql`, and `mysql`.
- Verifies table existence before writes.

### Create Strategy

- Uses append semantics (`if_table_exists="append"`).
- Supports `DataFrame` and `LazyFrame` inputs.

### Update Strategies (Upsert)

`update(...)` supports two upsert paths:

1. **Staging merge mode** (`is_temp_table=True`, default): writes to temp table, then merges into target table.
2. **Direct upsert mode** (`is_temp_table=False`): performs dialect-specific conflict update directly.

Before validation, update flow also auto-fills missing model columns by fetching current DB values using primary keys.

## Operational Assumptions

The kit is opinionated. These are part of normal operation, not optional conventions:

- Models should use UUID primary keys.
- PK and FK columns should define explicit `db_column`.
- `read()` expects `.values()` querysets.
- `delete()` is not currently exposed in `mo_crud_kit`.

## Troubleshooting the Kit

Most issues fall into validation contract mismatches:

1. **Column errors at create/update start:** check UUID PK and explicit `db_column` requirements.
2. **Rows unexpectedly invalid:** inspect validator error column (`POLARS_VALIDATOR_ERROR_COL` or `__error__info`).
3. **FK validation failures:** ensure related IDs exist in input frames or database.
4. **Read rejects queryset:** confirm `.values()` is called before `read()`.
5. **Update conflict behavior differs by DB:** verify chosen mode (`is_temp_table`) and target dialect support.
