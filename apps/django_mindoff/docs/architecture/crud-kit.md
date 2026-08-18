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
- Marks each row whose reference cannot be resolved in the error column, exactly
  as `ColumnValidator` and `RowValidator` mark theirs, and appends to any error
  the row already carries rather than replacing it. The pass never aborts the
  batch: a dangling reference is one invalid row, so the rest of the frame keeps
  its classification and `is_partial` decides the outcome — `True` writes the
  good rows and returns the bad ones in `invalid_model_frms`, `False` fails the
  operation without writing.
- Flags rows against an in-memory related frame with a join, so keys stay inside
  the engine and a `LazyFrame` takes the marking as plan nodes rather than being
  collected once per FK column — which matters most for a scan-backed frame,
  where each collect is a fresh read. Marking every row is more work than the
  bare "is anything bad?" count it replaces (about +80 MB and +0.14 s per
  million rows across four FK columns); that is the cost of knowing *which* rows
  are bad.
- Checks existence in bounded chunks rather than one `IN (...)` per column. The
  chunk size comes from the backend's own `max_query_params` where it reports
  one (SQLite's `SQLITE_MAX_VARIABLE_NUMBER`, 32766 by default — a hard error
  once exceeded), capped by a local ceiling that also covers the drivers
  reporting no limit because they interpolate client-side (psycopg2, MySQL); the
  smaller of the two wins. Set `MO_CRUD_FK_CHUNK_SIZE` to change that ceiling
  (default 10000). Unlike `MO_CRUD_ENGINE_CACHE_SIZE`, `0` is not an opt-out —
  an unbounded `IN (...)` is a hard failure rather than a memory trade-off, so a
  non-positive value falls back to the default.
  Within a single call, keys already proven to exist are not asked about again,
  so a second foreign key into the same table drops them from its `IN (...)` —
  fewer round trips and fewer bytes on the wire. The memo is capped at one chunk
  per related model: remembering every existing key would rebuild the very set
  the chunking exists to avoid holding, so past the cap the remainder is simply
  re-queried. It lives on the validator instance, which the pipeline builds
  fresh per call, so a cached "exists" never outlives the read behind it.
  Distinct values are held as an Arrow-backed column and only one chunk at a
  time becomes Python objects. Every chunk is queried: marking a row needs the
  values that are missing, not a count of them, so stopping at the first short
  chunk would leave the later chunks' bad rows unflagged. Only the missing
  values accumulate — the existing keys are consumed per chunk and dropped, so
  the full existing set is never resident.

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

### 6. Validate-Only (dry run)

`create(...)` and `update(...)` accept `is_validate_only` (default `False`). When
`True`, the pipeline above runs exactly as it would for a real write and returns
the same `(status, valid_model_frms, invalid_model_frms)` triple — but the write
is skipped, so the call is a drop-in preview of the one that would commit.

- The skip is total and level-independent. `none` and `columns_only` normally
  write directly; under `is_validate_only=True` neither does.
- Validation is not stubbed. At `validation_level="full"` the foreign-key stage
  still issues its real existence queries — they are reads, and a preview that
  skipped them would report a verdict the write would not agree with. An
  unresolved reference comes back as an invalid row in the preview, the same way
  it would from the real call.
- `batch_size` has no meaning in this mode: nothing is batched because nothing
  is written.
- `CRUDProcessor` is never constructed, not merely never called. Building one
  opens a connection and mutates the process-wide engine cache, so a preview
  stops short of it.

The consequence worth planning around: a dry run cannot surface what only the
write can raise. Integrity errors, the PK-only no-op warning, unsupported-backend
errors, and connection failures all originate in the write path, so a clean
preview is a statement about validation only.

The unsafe-write warning at `validation_level="none"` is deliberately still
emitted under `is_validate_only=True`. Nothing is written, so it is not a warning
about that call — but at that level nothing is validated either, so the preview's
`ok` carries no information about the write it stands in for, and the warning is
the only signal of that.

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

Reads take no `using` argument: the database comes from the queryset, so
`Model.objects.using("<alias>").values()` is what points a read at a non-default
database — including one provisioned at runtime and registered only in
`django.db.connections`. That works for both paths below; the ConnectorX URI is
resolved through the same target lookup the writers use, so a live-only alias
keeps the zero-copy path instead of quietly falling back to the cursor.

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

- Builds a SQLAlchemy engine from the target database's Django settings —
  resolved from `DATABASES`, or from a live connection when the caller names one
  (see [Choosing the target database](#choosing-the-target-database-using)).
- Supports `sqlite`, `postgresql`, and `mysql`.
- Table existence is verified lazily by reflection (a missing table raises a
  clear error) rather than scanning all table names on every operation.

### Choosing the target database (`using`)

`create(...)` and `update(...)` accept `using` to say *which* database the write
goes to. Reads take their database from the queryset instead — call
`Model.objects.using(...)` before handing it to `read(...)` or `read_batches(...)`.

| `using` | Resolves to | Use when |
| --- | --- | --- |
| omitted (`None`) | The default database, with routing left untouched | Ordinary single-database projects. This is the pre-existing behavior, unchanged. |
| `"alias"` | `settings.DATABASES["alias"]`, or a live connection registered under that alias | The database is configured, or provisioned at runtime and registered in `django.db.connections`. |
| a Django connection object | The connection's own `settings_dict` | Credentials are resolved at runtime and never written into settings at all. |

The alias and connection forms exist for **dynamic and multi-tenant databases**:
a tenant database provisioned on demand has no entry in `settings.DATABASES`, so
the settings lookup alone cannot reach it. Resolution therefore falls back to
`django.db.connections`, which is where such a connection lives.

One step of a write issues ORM queries rather than SQLAlchemy ones: foreign-key
validation at `validation_level="full"`. It is routed to the same target, so a
reference is checked where the rows are actually going, and it needs the
connection to be reachable by alias through `django.db.connections`. A connection
that is not registered there raises a clear error naming the alternative —
`validation_level="columns_only"`, which skips that query and writes through the
explicit connection alone.

### Per-process caching

To keep per-call latency low, two things are cached process-wide:

- **Engines** are cached per database alias + resolved connection params, so
  `create_engine` runs once per backend rather than on every CRUD call.
  PostgreSQL/MySQL engines (with their connection pools) are reused. The SQLite
  engine is the deliberate exception — it is bound to Django's live connection
  via a `creator`, so it is rebuilt each call to avoid holding a stale handle.
  The cache is bounded (least-recently-used, default 32, set
  `MO_CRUD_ENGINE_CACHE_SIZE` to change it or `0` to disable the ceiling).
  A fixed set of databases never reaches the limit; the bound matters when
  targeting many dynamic databases, where every engine would otherwise hold a
  connection pool open for the life of the process. Evicted engines are disposed
  (outside the cache lock, since closing sockets would otherwise stall every
  other engine lookup). Disposing an engine another thread is mid-transaction on
  is safe — SQLAlchemy closes only the idle pooled connections and swaps in a
  fresh pool, leaving checked-out connections to finish.

  Size the cache above the number of databases the process uses *concurrently*,
  not the number it uses in total. Past that point each miss evicts an engine
  that is about to be wanted again, so the cache thrashes and connections churn
  instead of pooling — the very cost it exists to avoid.
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

Each level answers "how much is checked". `is_validate_only` answers the separate
question "is anything written", and overrides every row in the table above — see
[Validate-Only (dry run)](#6-validate-only-dry-run).

`columns_only` deliberately skips the row pass, so field defaults,
`auto_now`/`auto_now_add` timestamps, UUID generation, and type coercion are
**not** applied — the caller must provide database-ready values for every
required column. Constraints the database itself enforces (NOT NULL, foreign
keys, types on type-strict backends) still apply at write time.

### Update Strategy (Upsert)

`update(...)` uses a staged-merge path: each frame is bulk-loaded into a per-call
staging table (using the same fast loader as `create()`), then merged into the
target with set-based SQL. No per-row Python materialization occurs, and the
whole update runs in one transaction.

**Only the columns the frame carries are written.** The staging table is built
from the frame, so it is the authority on what the merge may touch; a target
column absent from it appears in neither the assignment list nor the INSERT
column list. An existing row therefore keeps whatever it already holds, and a new
row takes the column's database default.

That property is why the merge is two statements rather than one
`INSERT ... ON CONFLICT`:

1. `UPDATE target SET <supplied> FROM staging WHERE target.pk = staging.pk` —
   assigns only the supplied columns. (MySQL has no `UPDATE ... FROM`, so
   SQLAlchemy renders the equivalent multi-table `UPDATE`.)
2. `INSERT INTO target (<supplied>) SELECT ... FROM staging LEFT JOIN target ...
   WHERE target.pk IS NULL` — adds only the primary keys that are genuinely new.
   The unmatched keys are found by anti-join rather than `NOT EXISTS` because
   MySQL refuses a subquery that reads the table being inserted into.

The upsert form cannot express this: it has to name every column it inserts, and
a `NOT NULL` column left out of that list fails the constraint *before* the
conflict is arbitrated — so it breaks even for rows that already exist. Splitting
the two cases is what makes a partial write possible at all. Both statements run
inside the same transaction.

Because nothing is read back before writing, two writers updating different
columns of the same row no longer overwrite each other. The previous behaviour
fetched every omitted column and put it back in the `SET` clause, so anything
committed between the fetch and the write was silently lost.

The projection is explicit rather than relying on the staging and target tables
happening to share a column order, since `INSERT ... FROM SELECT` pairs them
positionally. On PostgreSQL and MySQL each column is also cast to the target
column's type: the staging table is created from the frame's Polars schema, so a
UUID or timestamp arrives as text, which a strictly-typed backend will not write
into a typed column. SQLite needs no cast — its typing is dynamic.

Two consequences worth knowing:

- A frame carrying *only* the primary key has nothing to write. That is a no-op,
  and it emits a `RuntimeWarning` rather than passing silently.
- `update()` is an upsert, so a primary key that does not exist yet is an INSERT.
  If the frame omits a `NOT NULL` column with no database default, that INSERT
  fails with the database's own integrity error and the call rolls back. Use
  `create()` for genuinely new rows, or supply the column.

`skip_db_fill` is deprecated and inert. It used to skip the prefetch `SELECT`
that back-filled omitted columns; there is no back-fill left to skip, so passing
it changes nothing and warns. It is removed in 1.0.

### Larger-than-RAM Writes

Writes never materialize the full frame. Both `create()` and `update()` write in
bounded-memory chunks:

- **`LazyFrame` inputs** are streamed to a temporary Parquet file via the Polars
  streaming engine (the validation pipeline stays lazy until this point) and
  re-read in Arrow batches. Peak memory is one `batch_size` chunk, independent of
  dataset size. The sink is told the row-group size explicitly to make that true:
  pyarrow decodes a whole Parquet *row group* per batch, so leaving Polars to
  choose (row groups on the order of 10<sup>5</sup> rows) would bound peak memory
  by the row group rather than by the batch — still bounded, but much looser. For
  400k rows across 8 string columns at `batch_size=1000`, that is 33.6 MB of peak
  working set against 8.3 MB with batch-sized row groups. Row groups are floored
  at 1000 rows, so a very small `batch_size` cannot degenerate into one row group
  per row.
- **`DataFrame` inputs** are sliced into `batch_size` chunks. The slices are
  zero-copy views, so the incremental cost is bounded — but the caller already
  holds the whole frame in memory, so only a `LazyFrame` gives a genuinely
  larger-than-RAM write.

For staging-merge updates only the temp-table *load* is chunked — and it uses the
same per-backend bulk loader as `create()` (PostgreSQL `COPY`, MySQL `LOAD DATA
LOCAL INFILE`, SQLite `executemany`); the merge itself is set-based SQL, so it is
already larger-than-RAM friendly. All chunks for a model run inside one
transaction, so writes stay atomic.

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
5. **Update inserted nothing, or raised a NOT NULL error:** `update()` writes only the columns the frame carries. A primary key that already exists is updated in place; one that does not is inserted, and that insert must satisfy the table's constraints — supply the `NOT NULL` columns or use `create()`.
6. **MySQL write performance falls back to row binding:** if you see a `RuntimeWarning` about `LOAD DATA LOCAL INFILE`, set `local_infile=1` on the MySQL server (`SET GLOBAL local_infile = 1`) and ensure the client is connecting with `local_infile` enabled.
