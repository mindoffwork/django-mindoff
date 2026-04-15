# TDD Kit

The TDD Kit is the test-runtime infrastructure layer of `django-mindoff`. It provides deterministic, reusable testing primitives for apps, models, API invocation, and response-contract assertions.

It standardizes test setup so teams can focus on behavior contracts instead of boilerplate environment wiring.

For practical test-writing workflow, see [Developer Guide - Test-Driven Development](../developer_guide/test-driven-development.md).

## Architecture & Intent

The kit is built around one mixin surface (`MindoffTestCase`) plus one router-focused mixin (`MindoffRouterTestCase`):

- `MindoffTestCase` handles isolated app/model/runtime setup and API test helpers.
- `MindoffRouterTestCase` enforces version-router dispatch correctness.

This split keeps endpoint-behavior tests and router-behavior tests explicit and reusable.

## Core Runtime Components

| Component | Responsibility | Examples |
| --- | --- | --- |
| **`MindoffTestCase`** | Injected pytest fixtures for app/model mocking, API calls, response assertions, and users. | `self.mo_mock_app`, `self.mo_mock_call_api` |
| **`MindoffRouterTestCase`** | Reusable assertions for `VERSION_MAP` routing behavior. | dispatch and unknown-version checks |
| **Dynamic resource helpers** | Build and teardown temporary apps/models/tables safely. | `_ensure_dynamic_app`, `_create_model`, `_cleanup_dynamic_app` |
| **Frame fixture helpers** | Generate and mutate model-backed Polars fixtures. | `mo_mock_model_frms`, `mo_update_mock_model_frms` |

## Fixture Injection Lifecycle

`MindoffTestCase.run` is an autouse fixture that injects helper surfaces into each test instance:

- `self.mo_mock_app`
- `self.mo_mock_model`
- `self.mo_mock_model_frms`
- `self.mo_update_mock_model_frms`
- `self.mo_mock_call_api`
- `self.mo_assert_api_response`
- `self.mo_mock_user`
- `self.asserts`
- `self.client` (`APIClient`)

It also resets `mo_validation_kit` state when available to avoid cross-test aggregate contamination.

## Dynamic App & Model Runtime

### Temporary App Isolation (`mo_mock_app`)

`mo_mock_app(...)` creates a temporary Django app at runtime and wires it into test execution:

1. Creates temp app package and URL modules.
2. Inserts temp path into `sys.path`.
3. Applies `override_settings` for `INSTALLED_APPS` and root URLConf.
4. Clears app/url caches for resolver consistency.
5. Registers teardown that removes modules, path injections, URL cache state, and temp files.

### Dynamic Model Engineering (`mo_mock_model`)

`mo_mock_model(...)` builds runtime model classes and real DB tables:

1. Validates naming and FK parameters.
2. Resolves current app context or creates isolated dynamic app.
3. Generates model class with UUID PK and optional FK/field definitions.
4. Creates table via schema editor.
5. Registers teardown to drop created tables and cleanup dynamic app state.

This allows integration-style tests against actual ORM tables without permanent project mutations.

## Polars Fixture Architecture

### Model-to-Frame Generation (`mo_mock_model_frms`)

Creates `dict[ModelClass, DataFrame]` fixtures from bakery-prepared objects:

- Supports per-model row counts.
- Supports per-model excluded columns.
- Supports row-level modifications (`modify` map).
- Can enforce DB-column naming.
- Can flatten FK values to ids.
- Supports UUID generation mode controls.

### Controlled Frame Mutation (`mo_update_mock_model_frms`)

Updates existing frame fixtures with newly generated values while protecting key relational columns:

- PK and FK columns are always protected.
- User-specified keep columns are merged with protected columns.
- Non-protected columns are replaced with generated fixture values.
- Optional post-update row modifications are applied.

This preserves relational integrity while enabling update-focused test scenarios.

## API Invocation Architecture

`mo_mock_call_api(...)` provides URL-name driven API invocation with runtime policy checks:

1. Resolves API URL and detects versioned routing.
2. Resolves API class and reads configured HTTP `method`.
3. Enforces allowed methods (`get|post|put|patch|delete`).
4. Rejects payload for `GET/DELETE` (requires query params instead).
5. Applies authentication and headers.
6. Forces queue-mode APIs into direct execution for deterministic testing.
7. Executes request through DRF `APIClient`.

This creates one consistent API-call surface across sync and queue-configured endpoints.

## Response Contract Assertions

`mo_assert_api_response(...)` is a contract assertion layer for response shape/media expectations:

- Asserts response existence.
- Asserts exact status code.
- Asserts content type by expected response category:
- `json`
- `plain`
- `html`
- `binary`
- `others` (status-only contract)

This keeps tests aligned with transport-level API contracts, not only payload fragments.

## User Fixture Strategy

`mo_mock_user(...)` creates or reuses auth users:

- Reuses existing user when username already exists.
- Creates user with bakery otherwise.
- Sets password when provided.

This makes authenticated test setup stable and repeatable.

## Router Verification Layer

`MindoffRouterTestCase` validates version-router behavior:

1. Every configured `VERSION_MAP` entry dispatches to expected class (`as_view` call path).
2. Unknown versions return 404 with `INVALID_API_VERSION` and `available_versions` payload.

This guards version-routing correctness independent of endpoint business logic.

## Safety & Cleanup Model

The kit aggressively cleans mutable test runtime state:

- URL resolver caches are cleared during setup/teardown.
- Dynamically imported modules are removed from `sys.modules`.
- Temporary filesystem paths are removed.
- Temporary DB tables are dropped.
- App registry caches are refreshed.

These safeguards reduce flaky behavior from shared global Django runtime state.

## Troubleshooting the Kit

1. **Versioned API call fails unexpectedly:** ensure `url_kwargs` includes `version`.
2. **GET/DELETE test raises payload error:** move data into `query_params`.
3. **Dynamic app/model leakage between tests:** verify test uses provided helpers and allows fixture teardown to run.
4. **Frame updates break relations:** check `keep_columns` and ensure PK/FK columns are not overridden.
5. **Router tests fail for missing version:** confirm target version exists in router `VERSION_MAP`.
