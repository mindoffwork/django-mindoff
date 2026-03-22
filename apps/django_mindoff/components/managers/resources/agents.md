# Mindoff Coding Agent Guide

This project uses `django-mindoff` pip package. Follow these rules whenever generating or editing code.

## Primary Rule

- Prefer Mindoff kits over ad-hoc hand-written plumbing.
- Keep logic aligned with existing app architecture and file placement conventions.

## Priority Order (Always Optimize In This Order)

- Choose approaches that maximize:
  - high quality and correctness
  - maintainability, readability, and traceability
  - lean/lightweight implementation (avoid unnecessary code)
  - clean and consistent design
  - efficiency and performance
- When tradeoffs exist, prefer the option that best satisfies the above priorities with minimum complexity.

## Imports and Entry Points (Use Exactly)

- Validation kit: `from django_mindoff.components.validation_kit import mo_validation_kit`
- CRUD kit: `from django_mindoff.components.crud_kit import mo_crud_kit`
- Polars kit: `from django_mindoff.components.polars_kit import mo_polars_kit`
- Helper kit: `from django_mindoff.components.helper_kit import mo_helper_kit`
- API kit: `from django_mindoff.components.api_kit import MindoffAPIMixin, mo_api_kit`
- Response kit: `from django_mindoff.components.response_kit import mo_response_kit`

Important:

- Call validation methods through `mo_validation_kit.<method>(...)` only.
- Do not bypass kit entry points by importing private internals unless explicitly required.

## Decision Tree for New Logic

1. If the task is tabular/bulk/model-frame oriented, use Polars + CRUD kit.
2. If the task is regular row/object payload flow without Polars need, use serializers and standard Python logic.
3. If mixed workload is best, use hybrid:
   - serializers/request guards for boundary validation
   - Polars + `mo_crud_kit` for heavy transforms/writes

## CRUD and Polars Guidance

- Use `mo_crud_kit.create/read/update` for model-aware bulk operations.
- `mo_crud_kit.read()` must receive queryset with `.values()`.
- Treat `status` from CRUD (`ok`, `partial_ok`, `fail`) as part of control flow.
- Inspect invalid rows from `invalid_model_frms` and the configured error column.
- Use `mo_polars_kit` helpers for frame normalization, null handling, emptiness checks, and frame type sync.
- Use Mindoff model-frame format (`model_frms`) where it provides real benefit.
  - Format: `{ModelClass: pl.DataFrame | pl.LazyFrame}`
  - Prefer this for bulk/tabular/multi-model flows where it reduces boilerplate and improves clarity.
  - Do not force `model_frms` for simple non-tabular logic where regular Python/serializer flow is clearer.
- Respect current limitations:
  - no `mo_crud_kit.delete()` public API
  - UUID model PK expectations
  - explicit `db_column` expectations for PK/FK in CRUD validation flow

## Validation First Policy

- Prefer `mo_validation_kit.ensure_*` checks over manual `if` blocks.
- Use `is_exception=True` for immediate failure branches.
- Use `is_aggregate=True` + `finalize(...)` when collecting multiple validation errors.
- Aggregate-mode safety rule:
  - If `is_aggregate=True` is used, do not proceed to business logic/CRUD layer until aggregate errors are explicitly checked.
  - Use `mo_validation_kit.has_errors()` when available in the installed version.
  - If `has_errors()` is unavailable, enforce gating via `finalize(...)` flow (for example `return_mode="list"` and branch on non-empty errors, or `return_mode="error"/"exception"` to fail-fast).
- Fallback to plain `if` conditions only when:
  - validation kit is not suitable for the specific case, or
  - the user explicitly requests plain conditions.

## API Authoring Rules

- Build APIs as `MindoffAPIMixin` subclasses unless a different pattern is explicitly requested.
- Set API class configuration (`api_url_name`, `api_name`, `api_description`, `method`, process/payload options) as class attributes.
- Return through `mo_response_kit` to keep response envelope consistent.
- For queue workloads, use queue mode and progress checkpoints where appropriate.
- Before implementing API logic, review available API kit attributes/parameters and use built-in features instead of custom workarounds.
- For request logic (for example `POST`), set required API attributes properly (method, payload schema/validation, limits, auth/permissions, process mode) based on user needs.
- Use only supported `payload_schema` forms for the current installed version; do not invent schema formats.
  - Supported forms include primitive types, dict/list schema shorthand, typed list/dict, `Union`, `Literal`, and optional (`Union[..., None]`) as documented for the current version.
  - If uncertain, verify from local package code/docs before writing schema.

## Serializer vs Polars Choice

- Prefer serializers when:
  - payloads are small or strongly object-shaped
  - business rules are request/field-centric
- Prefer Polars + CRUD kit when:
  - payloads are bulk/high-volume/tabular
  - transform/merge/filter steps are data-frame oriented
- Prefer hybrid when both are true in the same API.

## Project Structure Rules

- Follow Mindoff generated structure; do not invent arbitrary layout.
- Keep helper/business support logic under each app's `components/` folder.
- Keep APIs under `apps/<app_name>/apis/`.
- Keep tests in app test structure (`tests/`, `test_apis/`, `test_views.py`, etc.) and add/extend tests with each behavior change.
- Respect existing naming and routing conventions (`<app>__<api>` route names, version routers in `views.py`).

## Model and Foreign Key Rules

- For new models, default PK to UUID for Mindoff compatibility:
  - `id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")`
- Primary key must always be named `id` and `db_column` must always be `"id"`.
- If you detect non-UUID primary keys (or non-`id` PK naming/column), explicitly notify the user about `django-mindoff` CRUD incompatibility risk.
- Foreign key naming must follow Mindoff conventions:
  - field name ends with `_ref`
  - database column ends with `_ref_id` (via `db_column="<field_name>_id"` where field name is already `_ref`)
- Follow manager behavior (`create_model_field` and create flow normalization) for FK naming; do not invent alternate FK naming patterns.

## Test Writing Conventions (Mindoff TDD Kit)

- Prefer Mindoff test kit over ad-hoc test utilities:
  - `from django_mindoff.components.tdd_kit import MindoffTestCase`
  - `from django_mindoff.components.tdd_kit import MindoffRouterTestCase` (for router/version tests)
- Prefer class-based test cases for APIs.
- Organize by scenario/feature:
  - One API test class for the API baseline behavior.
  - Use separate test methods for scenarios.
  - If a feature area is large, create a dedicated test class for that feature.
- Use pytest-native style for tests:
  - prefer plain `assert` over unittest assertion methods
  - prefer `pytest.raises(...)` for exception assertions
  - prefer `@pytest.mark.parametrize` for matrix/scenario coverage
  - prefer reusable pytest fixtures for setup/teardown
- Leverage `self` extensions provided by `MindoffTestCase` instead of re-implementing fixtures/utilities:
  - `self.mo_mock_call_api`
  - `self.mo_assert_api_response`
  - `self.mo_mock_user`
  - `self.mo_mock_app`
  - `self.mo_mock_model`
  - `self.mo_mock_model_frms`
  - `self.mo_update_mock_model_frms`
- Avoid unittest-style assertion helpers unless maintaining existing legacy tests that already use them.
- Keep tests behavior-first and scenario-named (`test_acceptance_*`, `test_rejection_*`, `test_boundary_*` where applicable).
- For API tests, keep route identity consistent by using the same `api_url_name` in both call and assertion helpers.
- Review available helper parameters before writing tests so built-in features are fully used:
  - `self.mo_mock_call_api(...)` inputs like payload, query params, headers, url kwargs, user, etc.
  - `self.mo_assert_api_response(...)` expected status/response-type/code/data checks as needed by scenario.

## Scaffolding and Consistency

- Prefer `python mindoff.py create` flows for app/model/api scaffolding.
- For creating apps, models, and foreign keys, use Mindoff CLI by default so folder/module structure remains valid and non-hallucinated.
- Preserve managed markers/comments used by generators.
- Do not break `settings.py` and `urls.py` wiring conventions established by init/create flows.

## Documentation Version Discipline

- When referencing docs, always use the documentation version that matches:
  - installed `django-mindoff` package version
  - installed Django major/minor version in the user environment
- Do not rely on latest docs by default when project/runtime version may differ.

## API Version Upgrade Rules (V1 -> V2+)

- Never overwrite an existing API version class when the request is to evolve behavior.
- Prefer additive versioning:
  - keep existing `V1` class intact
  - create `V2` (or next version) class
  - update router `VERSION_MAP` with the new version mapping
  - preserve backward compatibility unless user explicitly asks to break it
- If asked to "modify" an existing API and change is behavior-affecting, confirm with user before upgrading version:
  - ask whether they want a new version (`V2`) instead of overwriting `V1`
  - proceed only after explicit confirmation
- If user explicitly asks to overwrite an existing version, still warn about backward-compatibility impact before proceeding.
- When creating a new API version, add/adjust tests for both:
  - new version behavior
  - router version resolution expectations

## Error Handling and Responses

- Use `MindoffValidationError` patterns and `code/category/data` contracts consistently.
- Keep response codes aligned with `config/responses.csv`.
- Avoid raw unstructured exception responses in API code.

## Practical Quality Bar

- Keep code minimal, explicit, and aligned with existing kit patterns in this repo.
- Reuse helper functions from kits before adding new utilities.
- Add tests for new behavior paths (success, fail, edge cases), especially around validation and CRUD status handling.
