# Data Operations (CRUD)

Use `mo_crud_kit` for model-aware bulk create/read/update workflows with Polars frames.
This workflow is designed for data-heavy endpoints where serializer-by-row patterns become a bottleneck. It applies directly to high-volume ingestion and update APIs.

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
from django_mindoff.components.crud_kit import mo_crud_kit
from apps.orders.models import OrderModel

# CREATE
create_status, create_valid, create_invalid = mo_crud_kit.create(
    {OrderModel: order_df},
    is_validate=True,
    is_partial=False,
)

# READ (queryset must use values())
orders_frm, stats = mo_crud_kit.read(
    OrderModel.objects.filter(is_active=True).values(),
    page_number=1,
    batch_size=100,
)

# UPDATE
update_status, update_valid, update_invalid = mo_crud_kit.update(
    {OrderModel: order_df},
    is_validate=True,
    is_partial=True,
    is_temp_table=True,
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
