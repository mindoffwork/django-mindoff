# Data Operations (CRUD)

Use `mo_crud_kit` for model-aware bulk create/read/update workflows with Polars frames.
This workflow is designed for data-heavy endpoints where serializer-by-row patterns become a bottleneck. It applies directly to high-volume ingestion and update APIs.

<div class="admonition note">
<p class="admonition-title">When to use mo_crud_kit — and when not to</p>
<p><code>mo_crud_kit</code> is designed for workflows where data arrives or is consumed in bulk: ingestion pipelines, large exports, high-volume upserts, or any operation where the dataset is large enough that row-by-row serializer overhead or <code>bulk_create</code> become the bottleneck. It works on Polars <code>DataFrame</code> and <code>LazyFrame</code> inputs — not on individual model instances.</p>
<p>For everyday operations — creating a single record, updating a user profile, returning a list of 20 results — <strong>use native Django serializers</strong>. They are simpler, easier to debug, and the right tool at that scale. Reach for <code>mo_crud_kit</code> when volume is the problem. The two approaches are complementary; most projects use both.</p>
</div>

## Prerequisites

- Models are migrated and uses UUID primary/foreign keys.

## Implementation

`mo_crud_kit` provides model-aware operations for bulk `create`, `read`, and `update`.
It uses model metadata plus Polars validation to keep writes fast and consistent.

### 1. Model Frames

`mo_crud_kit` works on a model-frame mapping:

```python
{
    OrderModel: order_df_or_lazy,
    OrderItemModel: item_df_or_lazy,
}
```

Each write operation returns:

- `status`: `ok`, `partial_ok`, or `fail`
- `valid_model_frms`: rows that passed validation
- `invalid_model_frms`: rows with error metadata

**Sample Model Frame**

```python
import polars as pl
from apps.orders.models import OrderModel

order_df = pl.DataFrame(
    {
        "order_id": ["f2fa1a5b7abf4f37a3f7e14725c0b211"],
        "status": ["draft"],
        "total_amount": [120.50],
    }
)

model_frms = {
    OrderModel: order_df,
}
```

{{ MO_CRUD_KIT_FUNCTIONS }}

### Example Usage

```python
from django_mindoff import mo_crud_kit
from apps.orders.models import OrderModel

# CREATE (full validation, written in batch_size chunks via the backend's fast
# bulk loader: PostgreSQL COPY / MySQL LOAD DATA / SQLite executemany)
create_status, create_valid, create_invalid = mo_crud_kit.create(
    {OrderModel: order_df},
    validation_level="full",   # "full" | "columns_only" | "none"
    is_partial=False,
    batch_size=1000,
)

# CREATE (fast path: caller guarantees clean, DB-ready rows — skip row+FK passes)
mo_crud_kit.create(
    {OrderModel: order_df},
    validation_level="columns_only",
)

# READ (queryset must use values())
orders_frm, stats = mo_crud_kit.read(
    OrderModel.objects.filter(is_active=True).values(),
    page_number=1,
    batch_size=100,
    with_stats=False,   # skip exists()/count() for the fastest read
)

# LARGER-THAN-RAM: a real lazy scan (streamed to disk, scanned lazily)
lazy_frm, _ = mo_crud_kit.read(
    OrderModel.objects.all().values(), is_lazy=True
)

# LARGER-THAN-RAM: process in memory-bounded chunks (never concatenated)
for chunk in mo_crud_kit.read_batches(
    OrderModel.objects.all().values(), batch_size=10_000
):
    handle(chunk)

# UPDATE (staged merge: bulk-load staging table, then set-based SQL merge)
update_status, update_valid, update_invalid = mo_crud_kit.update(
    {OrderModel: order_df},
    validation_level="full",   # "full" | "columns_only" | "none"
    is_partial=True,
    # skip_db_fill=True,  # skip the missing-column prefetch when the frame
    #                     # already has every column (e.g. a full read() frame)
)
```

## Core Concepts

### 1. Polars Serialization

`mo_crud_kit` uses model metadata plus Polars validators (`ColumnValidator`, `RowValidator`, `ForeignKeyValidator`) to sanitize and validate rows before DB writes.
This is the intended replacement for serializer-driven bulk validation in data-heavy pipelines.

What this gives you:

- Type normalization aligned with Django field definitions.
- Constraint checks (required/nullability, choices, min/max, length, FK consistency).
- Structured invalid-row capture in the configured error column (`POLARS_VALIDATOR_ERROR_COL`, default `__error__info`).

<div class="admonition warning">
<p class="admonition-title">Validate before write</p>
<p><code>mo_crud_kit</code> is built for validated tabular data. If you choose to skip the inbuilt validation + serialization, cover request-level validation in API code before sending it to CRUD Kit.</p>
</div>

### 2. Limitations

- `ManyToManyField` is not supported in row validation.
- `BinaryField` is not supported in row validation.
- Any Django field not mapped in row validator dtype map is unsupported.
- Models must use UUID primary keys for CRUD validation flow.
- Primary key and foreign key fields are expected to define explicit `db_column`.
- `mo_crud_kit.read()` requires queryset `.values()` input.
- `mo_crud_kit.delete()` is not currently exposed.

## Troubleshooting

- `read()` fails with shape/type errors  
  Confirm queryset input uses `.values()` and field names align with frame columns.
- Rows are silently excluded from writes  
  Inspect `invalid_model_frms` and the configured error column to trace validation failures.
- FK validation fails unexpectedly  
  Check UUID types and explicit `db_column` configuration on related fields.
