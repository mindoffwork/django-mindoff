# API Kit

The API Kit is the runtime backbone of `django-mindoff`. It defines how requests are routed, validated, executed, and finally returned as a consistent response envelope.

One pipeline supports both synchronous (`direct`) and asynchronous (`queue`) APIs.

For API authoring workflow and endpoint implementation examples, refer to the Developer Guide:

- [API Development](../developer_guide/api-development.md)
- [Queue Mode API](../developer_guide/queued-api-processing.md)

## Architecture & Request Flow

At a high level, requests follow one shared path and branch only when execution starts (`direct` vs `queue`).

### Flow Diagram

<figure>
  <img
    src="../../_assets/architecture/api_queue_workflow_diagram.svg"
    alt="API Kit request flow showing APIVersionRouter resolution, MindoffAPIMixin request guards, direct execution, queue enqueue and worker execution, and response assembly"
  />
  <figcaption><em>Figure: End-to-end API Kit request lifecycle across direct and queue modes.</em></figcaption>
</figure>

### Core Runtime Components

- **`APIVersionRouter`**: Entry point for versioned APIs. It reads `version` from URL kwargs (default is `1`), resolves the target class from `VERSION_MAP`, and dispatches through `as_view()`. If a version is missing in `VERSION_MAP`, it returns `INVALID_API_VERSION` (including `available_versions`). When `MINDOFF_USE_VIEW_CACHE=True`, view callables are cached per version.
- **`MindoffAPIMixin`**: Lifecycle owner for class-based APIs. It extends DRF `APIView` and treats class attributes as policy (`method`, auth, permissions, payload limits/schema, rate limits, process mode, queue controls). `initial()` runs request-time guards, and `_handle_request_logic()` selects `direct` vs `queue`.
- **`mo_response_kit`**: Response assembler. It builds predictable envelopes based on `responses.csv` metadata.

## Dual-Phase Validation

Validation is intentionally split into two phases: startup checks and request-time checks.

### 1. Startup Configuration Checks

At startup, `checks.py` scans installed apps for `apis/` packages, imports discovered modules, and validates every `MindoffAPIMixin` subclass it finds.

- **Dependency checks:** Verifies required integration dependencies: `djangorestframework`, `python-decouple`, `django-ratelimit`, `typeguard`, `polars`, `pandas`, `sqlalchemy`, `orjson`, `pyarrow`, `dramatiq`, `redis`.
- **Core API importability:** Confirms `MindoffAPIMixin` itself can be imported.
- **Class configuration checks:** Instantiates each API class and runs `validate_api_configuration()`.

This catches issues like:

- Invalid HTTP `method` (must be `get|post|put|delete`)
- Missing API identity fields (`api_url_name`, `api_name`, `api_description`)
- Missing `api_url_name` in URL resolver
- Invalid payload/rate-limit/process-mode settings
- Invalid `progress_steps` structure/order for queue-mode APIs

If one module under `apis/` fails to import, that module is skipped and the scan continues.

### 2. Per-Request Guards

For each request, `MindoffAPIMixin.initial()` applies:

- **Authentication and permissions:** DRF `initial()` runs first.
- **Method enforcement:** `_initial_validate_request_method` requires exact match with configured `method`. Failure code: `INVALID_METHOD`.
- **API rate limit:** `_initial_validate_api_rate_limit` applies `api_request_limit` using `django-ratelimit` with `key="user_or_ip"`. Failure code: `API_RATE_LIMITED`.
- **Payload checks (`POST`/`PUT` only):** `_initial_validate_request_payload` enforces size (from `CONTENT_LENGTH`) and runs schema/depth validation when enabled.

## Payload Schema Intelligence

Payload validation runs through `validate_schema(...)` and is finalized as `INVALID_PAYLOAD` if aggregate errors are present.

- **Size check:** If `max_payload_size` is set and `CONTENT_LENGTH` is present, request size in MB must be within limit. Failure code: `PAYLOAD_TOO_LARGE`.
- **Depth check:** If `max_payload_depth` is set, nesting depth must stay within limit.
- **Supported schema forms:** Native types (`str`, `int`, etc.), `Union[...]`, `Literal[...]`, list shorthand (`[item_schema]`), typing lists (`List[T]`/`list[T]`), typing dicts (`Dict[K, V]`/`dict[K, V]`), and dict shorthand (`{"key": subschema}`).
- **Current union behavior:** If `None` is allowed in `Union`, `None` is accepted. Otherwise, validation currently follows the first non-`None` branch.
- **`strict` vs `basic` (`payload_validation`):** In **`strict`** mode, missing declared dict keys are validation errors. In **`basic`** mode, missing declared keys are ignored.
- In both modes, declared keys (when present) are validated recursively, and undeclared extra keys are currently tolerated.
- If `payload_validation` is enabled and `payload_schema is None`, only empty payloads are accepted; non-empty payload returns `PAYLOAD_NOT_ALLOWED`.

## Rate Limiting & Queue Controls

Rate limits protect both the main API endpoint and queue control endpoints.

### Standard & Queue Control Limits

- **`api_request_limit`**: Per user/IP limit for main endpoint calls. Failure code: `API_RATE_LIMITED`.
- **`queue_detail_api_limit`**: Limit for queue result/detail endpoint.
- **`queue_cancel_api_limit`**: Limit for queue cancellation endpoint.
- **`queue_retry_api_limit`**: Limit for queue retry endpoint.

Queue control endpoints use task-scoped `django-ratelimit` groups (for example: `<api_url_name>_<queue_id>_cancel`).

### SSE Stream Concurrency Slotting

Queue status streaming uses slot-based concurrent connection limiting, not request-window throttling.

- **Backend:** Redis slot acquire/release
- **Config:** `queue_status_stream_api_limit`
- **Failure:** `RATE_LIMITED` with message `Too many active streams`

## Execution Modes

### Direct Mode (Synchronous)

Used when `process_mode="direct"`.

1. Request guard pipeline runs (see [Per-Request Guards (`initial`)](#2-per-request-guards)).
2. `_handle_request_logic()` calls `run(...)` directly.
3. Response is returned via `mo_response_kit` (or an explicit response object).

### Queue Mode (Asynchronous)

Used when `process_mode="queue"`.

1. Multipart/file uploads are rejected in async mode.
2. Request context is enqueued via `enqueue_process`.
3. Response returns `QUEUED` with `queue_id`, `status_stream_url`, `response_url`, `cancel_url`, `retry_url`, and `progress_steps` (configured class value or `{}`).
4. Worker executes `run()` in the background and can publish progress via `progress_checkpoint(...)`. Final payloads are persisted for queue control endpoints.

## Exception Normalization

`_resolve_api_exception(...)` is the central exception mapper.

### Where Exceptions Are Caught

1. **Class-based APIs:** `MindoffAPIMixin.dispatch()` wraps DRF dispatch and routes failures to `handle_exception()`.
2. **Queue enqueue path:** Queue connectivity failures are mapped to `QUEUE_SERVICE_UNAVAILABLE`.
3. **`api_guardian` decorator:** Function-based wrapper that catches exceptions and normalizes responses.

<div class="admonition warning">
<p class="admonition-title">Warning</p>
<p><code>api_guardian</code> is internal and considered unstable. It may change or be removed in a future release without being treated as a major-version breaking change. It only normalizes exceptions. It does <em>not</em> provide the full <code>MindoffAPIMixin</code> lifecycle (no <code>initial()</code> guard pipeline, no class configuration checks, no queue-mode helper surface).</p>
</div>

### Exception Resolver Mapping

| Source Exception             | Normalized Code                                         |
| ---------------------------- | ------------------------------------------------------- |
| DRF `NotAuthenticated`       | `NOT_AUTHENTICATED`                                     |
| DRF `AuthenticationFailed`   | `AUTHENTICATION_FAILED`                                 |
| DRF `PermissionDenied`       | `PERMISSION_DENIED`                                     |
| DRF `Throttled`              | `RATE_LIMITED`                                          |
| **`MindoffValidationError`** | Uses the exception's own `code`, `category`, and `data` |
| _Any Other Exception_        | `UNEXPECTED_ERR` (if no explicit `code` is present)     |

## Troubleshooting the Kit

When troubleshooting, start here:

- **Validation seems skipped:** Payload checks only run for `POST`/`PUT`. Verify request method, `payload_validation`, and `payload_schema`.
- **`INVALID_API_VERSION` returned:** Check router `VERSION_MAP` and the requested URL version.
- **`API_CONFIG_ERR` at startup:** Recheck the rules under [Startup Configuration Checks (`checks.py`)](#1-startup-configuration-checks).
- **Unexpected throttling or rate-limit errors:** Recheck limits under [Rate Limiting & Queue Controls](#rate-limiting-queue-controls).
- **Queue accepted but not progressing:** Verify worker availability and Redis/Dramatiq connectivity.
