# CRUD Kit

The CRUD Kit is the data-engineering layer of `django-mindoff`. It runs model-aware bulk `create`, `read`, and `update` operations over Polars frames while enforcing Django model constraints.

It is designed for high-throughput tabular workflows where API-level validation and storage-level consistency both matter. It complements Django and DRF — it does not replace them. For individual records or small result sets, native Django serializers remain the simpler and more appropriate choice; use the CRUD Kit when dataset volume is the bottleneck.

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

`create(...)` and `update(...)` share the same ordered write pipeline at `validation_level="full"` (the default).

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

When `validation_level="none"`, CRUD writes proceed directly and emit runtime warnings about unsafe persistence.

## Read Path Architecture

`read(...)` is intentionally strict on input and flexible on output.

### Input Contract

- Queryset must be `.values()`-based.
- `batch_size` must be `>= 0`.
- When `batch_size=0`, defaults are auto-selected to `1000` for streaming mode and `100` for pagination mode.

### Execution Modes

1. **Streaming mode** (`page_number=None`): reads the full result set.
2. **Pagination mode** (`page_number=<n>`): returns only one page plus paginator metadata.

Each mode supports eager (`pl.DataFrame`) and lazy (`pl.LazyFrame`) output.

### Memory & Query Efficiency

| Control | Effect |
| --- | --- |
| `with_stats=False` | Skips the `exists()` + `count()` queries. `total_count`/`total_pages` become `None`; pagination derives `has_next` by fetching one extra row. The fastest path when totals aren't needed. |
| `is_lazy=True` (streaming) | A genuine larger-than-RAM scan: rows are streamed to a temporary Parquet file and the returned `LazyFrame` scans it on `collect()`. The temp file is removed when the frame is garbage-collected. |
| `mo_crud_kit.read_batches(qs, ...)` | Returns an iterator of Polars frames pulled `batch_size` rows at a time — never concatenated — so the whole result set is never held in memory. |

### Read Mechanism

There is no engine to choose — a read always takes the fastest path that can still return correct data:

- **ConnectorX** (`pl.read_database_uri`) for a zero-copy DB → Arrow transfer, used **only when the read is safe**. ConnectorX opens its own connection and cannot see uncommitted rows, so it is used only **outside an open transaction**.
- **Django's own cursor** otherwise — inside an open transaction (Django `TestCase`, `ATOMIC_REQUESTS`, `transaction.atomic()`), with in-memory SQLite, or when ConnectorX is unreachable. It reuses Django's connection (so parameters and transaction visibility behave normally) and builds the frame column-wise from row tuples — never list-of-dicts. Streaming reads (`is_lazy=True`, `read_batches`) always use this cursor path, since ConnectorX has no streaming cursor.

The fallback is transparent: callers never select or are warned about the path. Frames are normalized to the canonical model dtypes defined in `_crud_kit/dtypes.py` (`DJANGO_TO_POLARS_TYPE_MAP`) — the same mapping the create/update validators enforce — so a read frame can be fed straight back into `update()`. `JSONField` columns follow `json_column_mode` (`auto`/`object`/`text`); `auto` returns raw JSON text.

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
- Table existence is verified lazily by reflection (a missing table raises a
  clear error) rather than scanning all table names on every operation.

### Per-process caching

To keep per-call latency low, two things are cached process-wide:

- **Engines** are cached per database alias + resolved connection params, so
  `create_engine` runs once per backend rather than on every CRUD call.
  PostgreSQL/MySQL engines (with their connection pools) are reused. The SQLite
  engine is the deliberate exception — it is bound to Django's live connection
  via a `creator`, so it is rebuilt each call to avoid holding a stale handle.
- **Reflected `Table` metadata** is cached per engine. A cached (PG/MySQL)
  engine keeps its reflection warm across calls; the uncached SQLite engine gets
  fresh metadata each call (collected with the engine), so reflection always
  matches the current schema. Staging tables (unique per call) are never cached.

### Create Strategy

- Appends rows with each backend's fastest **same-connection** bulk loader, so
  the write stays inside the create's single transaction (all-or-nothing) and
  Polars' columnar data reaches the database without a per-row Python detour:
  - **PostgreSQL** — `COPY ... FROM STDIN` streamed from an in-memory CSV
    buffer via psycopg2's `copy_expert`.
  - **MySQL** — `LOAD DATA LOCAL INFILE` from a temp CSV (needs `local_infile`,
    which the engine enables on the client; falls back to row binding when the
    server forbids it and emits a `RuntimeWarning` — enable `local_infile` on the
    server to restore bulk-load performance).
  - **SQLite** — SQLAlchemy Core `insert()` executed as a DBAPI `executemany`;
    there is no bulk-load protocol, and this path is bound to Django's live
    connection so it works against an in-memory database.
- On SQLite the engine is bound to Django's autocommit-mode connection, so the
  create wraps every chunk in one explicit transaction (`BEGIN`) — without it
  each statement would auto-commit (one fsync per row).
- Honors `batch_size`: rows are written in bounded chunks (`LazyFrame` inputs
  are streamed through Parquet, eager frames are sliced), so create is both
  memory-bounded and competitive with — typically faster than — `bulk_create`.
- Supports `DataFrame` and `LazyFrame` inputs.

#### Validation levels

Both `create(...)` and `update(...)` accept `validation_level`
(`"full" | "columns_only" | "none"`, default `"full"`).

| Level | Pipeline | When to use |
| --- | --- | --- |
| `full` (default) | `ColumnValidator → RowValidator → ForeignKeyValidator` + valid/invalid split; honors `is_partial`. | Untrusted/raw input. |
| `columns_only` | `ColumnValidator` only — normalizes shape (rename to `db_column`, add missing/auto columns, drop extras), then writes. | Caller already validated rows/FKs upstream and supplies database-ready values. Lower overhead. |
| `none` | No validation (not even column normalization); writes as-is and warns. | Trusted, already-shaped frames only. |

`columns_only` deliberately skips the row pass, so field defaults,
`auto_now`/`auto_now_add` timestamps, UUID generation, and type coercion are
**not** applied — the caller must provide database-ready values for every
required column. Constraints the database itself enforces (NOT NULL, foreign
keys, types on type-strict backends) still apply at write time.

### Update Strategy (Upsert)

`update(...)` uses a single staged-merge path: each frame is bulk-loaded into a
per-call staging table (using the same fast loader as `create()`), then merged
into the target with one set-based dialect-specific statement
(`INSERT ... FROM SELECT ... ON CONFLICT/DUPLICATE`). No per-row Python
materialization occurs, and the whole update runs in one transaction.

Before validation, update flow also auto-fills missing model columns by fetching
current DB values using primary keys. Pass `skip_db_fill=True` to skip that
prefetch `SELECT` when the caller already supplies every column to be written
(for example a frame read in full via `read()` and modified in place) — primary
key canonicalization and the PK presence check still run, so upsert matching is
unaffected. Any column the frame omits is then written as `NULL` rather than
back-filled, so only enable it for column-complete frames.

### Larger-than-RAM Writes

Writes never materialize the full frame. Both `create()` and `update()` write in
bounded-memory chunks (`batch_size` rows at a time):

- **`LazyFrame` inputs** are streamed to a temporary Parquet file via the Polars
  streaming engine (the validation pipeline stays lazy until this point) and
  re-read in Arrow batches — peak memory is one batch, not the whole dataset.
- **`DataFrame` inputs** are sliced into `batch_size` chunks.

For staging-merge updates only the temp-table *load* is chunked — and it uses the
same per-backend bulk loader as `create()` (PostgreSQL `COPY`, MySQL `LOAD DATA
LOCAL INFILE`, SQLite `executemany`); the merge itself is a single set-based SQL
statement, so it is already larger-than-RAM friendly. All chunks for a model run
inside one transaction, so writes stay atomic.

### Keeping the Lazy Path Honest

When a `LazyFrame` flows through the pipeline, the validators never force an
early plan execution to inspect structure:

- Schema is resolved once per frame via `mo_polars_kit.resolve_schema(...)`
  (`collect_schema()` under the hood) and threaded through the per-field work,
  instead of touching `LazyFrame.schema`/`.columns` repeatedly. A schema is
  column-names-plus-dtypes only, so it stays valid across the row-level edits
  the validators apply; it is re-resolved only where a transform actually
  changes a column.
- `LazyFrame.schema` and `LazyFrame.columns` emit a Polars `PerformanceWarning`
  (each call re-resolves the whole accumulated plan). The test suite promotes
  that warning to an error while exercising the lazy create/read/update paths,
  so any accidental eager schema resolution fails CI rather than silently
  degrading the "lazy" guarantee.

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
5. **Update conflict behavior differs by DB:** verify the target dialect supports the staged-merge upsert (`ON CONFLICT`/`ON DUPLICATE KEY`).
6. **MySQL write performance falls back to row binding:** if you see a `RuntimeWarning` about `LOAD DATA LOCAL INFILE`, set `local_infile=1` on the MySQL server (`SET GLOBAL local_infile = 1`) and ensure the client is connecting with `local_infile` enabled.
