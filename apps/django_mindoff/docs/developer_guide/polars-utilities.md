# Polars Utilities

`mo_polars_kit` provides frame-level utilities for transformation, normalization, and null handling in Polars-based API workflows. If you are preparing frames for bulk writes, review [Data Operations (CRUD)](data-operations-crud.md).

<div class="admonition warning">
<p class="admonition-title">Stability Warning</p>
<p>`mo_polars_kit` is not feature-complete yet and is currently experimental and subject to change without any prior notice. Refer to this page to keep informed on development with Polars utilities.</p>
</div>

## Implementation

Start by importing the polars kit in your API or utility module:

```python
from django_mindoff import mo_polars_kit
```

{{ MO_POLARS_KIT_FUNCTIONS }}

### Example Usage

```python
import polars as pl
from django_mindoff import mo_polars_kit

frm = pl.DataFrame({
    "email": ["A@EXAMPLE.COM", None],
    "status": [None, "active"],
})

normalized = mo_polars_kit.frm_fill_notnull(
    frm,
    column="email",
    fill_value=lambda row: row.lower(),
    mode="map",
    row_param="row",
    dtype=pl.Utf8,
)

filled = mo_polars_kit.frm_fill_null(
    normalized,
    column="status",
    fill_value="draft",
    mode="lit",
)
```

## Troubleshooting

- `frm_fill_null` and `frm_fill_notnull` support `lit`, `map`, and `sink_map` modes.
- Use `sink_map` for lazy/streaming pipelines where full in-memory materialization is not desired.
