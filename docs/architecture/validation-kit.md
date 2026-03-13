# Validation Kit

The Validation Kit is the rule-enforcement layer of `django-mindoff`. It provides a single validation surface (`mo_validation_kit`) used across runtime components to enforce conditions, classify failures, and return predictable error structures.

It is designed to support three execution styles from the same API: immediate native exceptions, framework-structured validation errors, and aggregated multi-error validation.

For endpoint-level validation authoring, refer to [Developer Guide - Validations](../developer_guide/validations.md).

Validation Kit is not an isolated utility. It is part of core runtime behavior:

- API Kit uses it for request/config guards.
- Payload schema validation (`validate_schema`) depends on aggregate mode.
- Response Kit CSV loading uses it for dictionary contract checks.
- CRUD/Polars flows use it for operational guardrails.
- Queue/runtime exception normalization relies on `MindoffValidationError` shape (`code`, `message`, `data`).

## Core Runtime Components

| Component                    | Responsibility                                       | Notes                                                          |
| ---------------------------- | ---------------------------------------------------- | -------------------------------------------------------------- |
| **`MindoffValidator`**       | Public validation surface and aggregate-state owner. | Exposed as singleton `mo_validation_kit`.                      |
| **`MindoffValidationError`** | Structured framework validation exception.           | Carries `message`, `code`, `category`, `data`.                 |
| **`ValidationError`**        | Generic fallback exception type.                     | Used by `ensure(...)` and aggregate `exception` mode.          |
| **`_ErrorItem`**             | Internal buffered error record for aggregate mode.   | Stores function name, type, code, message, context, traceback. |
| **`_record_or_raise(...)`**  | Central decision engine for pass/fail behavior.      | Used by all validator methods.                                 |

## Validator Surface Map

Validation methods are grouped into consistent families.

### Equality & Comparison

- `ensure_equal`
- `ensure_not_equal`
- `ensure_same`
- `ensure_not_same`
- `ensure_greater`
- `ensure_greater_equal`
- `ensure_lesser`
- `ensure_lesser_equal`
- `ensure_in_range`
- `ensure_not_in_range`
- `ensure_almost_equal`
- `ensure_not_almost_equal`

### Truthiness

- `ensure_falsey`
- `ensure_truthy`

### Types & Class Relationships

- `ensure_type`
- `ensure_not_type`
- `ensure_subclass`
- `ensure_not_subclass`

### Containers & Collection Semantics

- `ensure_in`
- `ensure_not_in`
- `ensure_count_equal`
- `ensure_count_not_equal`

### Numeric / Regex / Filesystem

- `ensure_finite`
- `ensure_regex`
- `ensure_not_regex`
- `ensure_path`
- `ensure_not_path`

### Custom Predicate

- `ensure` (boolean or callable checks; configurable `exc_type`)

### Aggregate Lifecycle

- `finalize`
- `reset`

## Unified Execution Model

Every `ensure_*` method delegates failure handling to `_record_or_raise(...)`.

### Decision Flow

1. Method computes `ok` and method-specific failure metadata (`exc_type`, `message`, `context`).
2. If `ok=True`, returns `True`.
3. If `ok=False`, an `_ErrorItem` is created with traceback snapshot (`mo_helper_kit.get_exact_traceback(skip=3)`).
4. If `is_aggregate=True`, error is buffered and method returns `None`.
5. Else if `is_exception=True`, method-specific native exception is raised (with `.code` attribute attached).
6. Else, `MindoffValidationError` is raised with structured `data`.

Priority is intentional: aggregate mode takes precedence over immediate exception mode.

## Error Contract Semantics

### `MindoffValidationError`

Structured exception with runtime-friendly fields:

- `message` (human-readable)
- `code` (default `VALIDATION_ERR`, customizable per call)
- `category` (default `danger`)
- `data` (must be `dict` or `list`)

If `data` is not `dict|list`, constructor raises `TypeError`.

### Native Exception Mode

When `is_exception=True`, each validator raises its method-specific exception class (for example `ValueError`, `TypeError`, `LookupError`, `KeyError`, `FileNotFoundError`, `FileExistsError`, or custom `exc_type` for `ensure`).

### Custom Code Propagation

All modes preserve caller-provided `code`:

- Attached to native raised exception as `.code`
- Included in `MindoffValidationError.code`
- Stored per buffered `_ErrorItem` in aggregate mode

## Aggregate Mode Architecture

Aggregate mode supports collecting multiple failures before emitting one result.

### Buffering

- Any `ensure_*` call with `is_aggregate=True` appends `_ErrorItem` to internal `_errors`.
- Calls continue; no exception is raised at point of failure.

### Finalization

`finalize(...)` drains `_errors` using `return_mode`:

1. `list`: returns list of normalized error dicts (`type`, `code`, `message`, `context`)
2. `error` (default): raises `MindoffValidationError` with aggregate `data`
3. `exception`: raises `ValidationError` with combined formatted text (includes traceback snapshots)

### Reset Semantics

`finalize(...)` always resets internal aggregate state in `finally`, including when it raises.
`reset()` can also be called explicitly to clear state.

## Method Family Behavior Details

### Comparison Helpers

- Comparison operators are guarded; operator failures are treated as invalid comparisons and mapped to `TypeError` paths.
- `ensure_equal` has iterable-aware comparison behavior for non-string/non-bytes/non-dict iterables by normalizing to `list(...)` before comparison.

### Type/Class Helpers

- `ensure_type`/`ensure_not_type` use `isinstance`.
- `ensure_subclass`/`ensure_not_subclass` catch invalid-subclass inputs and route them through failure paths (`TypeError` semantics).

### Membership Helpers

- `ensure_in`/`ensure_not_in` use membership checks.
- Dict membership failures are keyed to `KeyError`; non-dict containers use `LookupError`.
- Non-iterable membership targets fall back to `TypeError`.

### Count Helpers

- `ensure_count_equal`/`ensure_count_not_equal` use `collections.Counter`.
- Unhashable elements or broken iterables route to `TypeError` paths.

### Numeric/Regex/Path Helpers

- `ensure_finite` requires numeric input and `math.isfinite`.
- Regex helpers require string input and use full-match semantics.
- Path helpers validate filesystem existence/non-existence via `pathlib.Path.exists()`.

### Custom `ensure(...)`

- Accepts boolean or callable.
- Callable exceptions are captured and converted into failure state.
- Uses configurable `exc_type` (default `ValidationError`) when `is_exception=True`.

## Integration Across Runtime Layers

Validation Kit is embedded in multiple internal flows:

- **API Kit:** method checks, auth/payload limits, request guardrails.
- **Schema Validation (`_helper_kit/validate_schema.py`):** runs aggregate checks across nested payload structures, then calls `finalize(code="INVALID_PAYLOAD")`.
- **Response Kit:** validates `responses.csv` structure and values during load.
- **CRUD/Polars flows:** enforces operational preconditions (types, ranges, table/column assumptions).
- **TDD Kit and tests:** resets aggregate state between runs to avoid stale buffered errors.

## Operational Caveats

- `mo_validation_kit` is a singleton with mutable aggregate buffer (`_errors`); aggregate usage should be scoped carefully per execution path.
- Forgetting to call `finalize(...)` after aggregate checks means failures remain buffered and unreported for that flow.
- Mixing aggregate and immediate-exception patterns in one branch can create unclear failure contracts.
- In aggregate mode, `is_exception` does not trigger immediate raises; buffering behavior wins by design.

## Troubleshooting the Kit

1. **Missing aggregate errors in output:** ensure all aggregate checks are followed by `finalize(...)`.
2. **Unexpected exception class:** verify method-specific exception semantics and whether `is_exception=True` was used.
3. **Custom error code not visible:** confirm code was passed to each failing check or to `finalize(...)` (for top-level aggregate error mode).
4. **Intermittent aggregate contamination:** ensure `reset()`/`finalize()` runs on every aggregate path, including early exits.
5. **Schema validation output seems partial:** check `validate_schema(...)` mode/depth inputs and confirm aggregate errors are finalized.
