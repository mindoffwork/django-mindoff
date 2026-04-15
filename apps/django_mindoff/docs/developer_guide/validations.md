# Validation Utilities

Use validation to stop bad input early and return consistent, actionable errors to clients.

## Prerequisites

- [API Class](api-development.md) is scaffolded and routed.
- `config/responses.csv` and response codes for expected validation failures exists in your project.

## Implementation

Validation at request boundaries prevents invalid states from reaching business logic.
`django-mindoff` provides `mo_validation_kit` for checks and `MindoffValidationError` for structured failure payloads.

**Recommended flow:**

1. Validate request data with `mo_validation_kit.ensure_*` checks.
2. Use `is_exception=True` when you want immediate failure as exception.
3. Use `is_aggregate=True` with `finalize(...)` when you need to collect multiple field errors first.
4. Return envelope responses through [Response Kit](responses.md).

**Error Handling Modes**

`mo_validation_kit` supports three practical modes:

- Immediate exception: raise on first failure with `is_exception=True`.
- Structured validation error: default mode raises `MindoffValidationError`.
- Aggregated errors: accumulate with `is_aggregate=True`, then call `finalize(...)`.

<div class="admonition tip">
<p class="admonition-title">Choose one strategy per endpoint branch</p>
<p>Mixing immediate and aggregate validation in the same branch can make error contracts hard to reason about. Keep one clear strategy for each API path.</p>
</div>

{{ MO_VALIDATION_KIT_METHODS }}

## Troubleshooting

- Validation errors look inconsistent across APIs  
  Ensure all APIs use the same response code strategy in `config/responses.csv` and return through `mo_response_kit`.
- Some errors are missing in aggregate mode  
  Confirm `finalize(...)` is called after all `is_aggregate=True` checks.
