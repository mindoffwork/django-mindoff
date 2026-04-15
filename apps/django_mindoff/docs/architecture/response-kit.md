# Response Kit

The Response Kit is the contract layer for outbound API responses in `django-mindoff`. It converts response codes into stable envelopes, HTTP status values, and consistent client-facing message metadata.

It centralizes response behavior so API implementations focus on business logic, not per-endpoint response formatting.

For usage-level response authoring, refer to [Developer Guide - Responses](../developer_guide/responses.md).

Response behavior is a cross-cutting runtime contract:

- API Kit depends on it to normalize direct and queue responses.
- Queue persistence depends on code-to-status mapping for stored `response_code`.
- Client integrations depend on stable envelope shape and message code semantics.
- Operational debugging depends on consistent exception logging behavior.

## Architecture & Intent

The core design is code-driven response resolution:

1. API code returns through `mo_response_kit` helper methods.
2. Response metadata is resolved from an in-memory code map.
3. HTTP status and envelope `status` are derived deterministically.
4. A normalized response object is returned (`Response`, `FileResponse`, or `HttpResponse`).

## Core Runtime Components

| Component                     | Responsibility                                                          | Examples                                        |
| ----------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------- |
| **`MindoffResponseHandler`**  | Public response surface for JSON/file/text/html responses.              | `json_response`, `file_response`                |
| **`MINDOFF_RESPONSES` cache** | In-memory response dictionary keyed by code.                            | `SUCCESS`, `UNEXPECTED_ERR`                     |
| **CSV loaders**               | Load and validate response dictionary from project or package fallback. | `load_responses_csv`, `_load_response_defaults` |
| **Status normalizer**         | Converts HTTP status classes into envelope status labels.               | `_derive_status_from_http`                      |

## Startup Bootstrap Path

Response metadata is loaded during Django app startup:

1. `DjangoMindoffConfig.ready()` calls `load_responses_csv()`.
2. `MINDOFF_RESPONSES` cache is populated before runtime API handling.
3. API and queue components consume this cache for response assembly and status mapping.

This startup load is the control point that keeps runtime response behavior deterministic.

## Response Dictionary Lifecycle

`load_responses_csv(...)` drives response dictionary hydration and validation.

### Source Resolution Order

1. Project file: `config/responses.csv`
2. Package fallback: manager resources CSV (copied into project config when project file is missing)

### Required Contract

Required CSV headers:

- `code`
- `title`
- `description`
- `http_status`

Validation rules include:

- Required headers must exist.
- `code` must be non-empty and unique.
- `http_status` must be an integer in `100..599`.
- Required fields cannot be empty.
- `code` is normalized to uppercase during load.

When loading completes, defaults from package CSV are merged into runtime cache, and the fallback `UNEXPECTED_ERR` entry is enforced.

### Cache Semantics

- `MINDOFF_RESPONSES` is a process-local in-memory map.
- Reloading clears and repopulates the map (`clear()` + `update()`), so consumers read the latest resolved dictionary in-process.
- Runtime behavior therefore depends on startup/load correctness for each worker process.

## JSON Response Assembly

`json_response(...)` is the primary API envelope builder.

### Assembly Flow

1. Resolve input `code` from `MINDOFF_RESPONSES`.
2. If code is unknown/empty, recursively fallback to `UNEXPECTED_ERR`.
3. Derive envelope `status` from HTTP class (`2xx -> ok`, `4xx -> fail`, `5xx -> exception`).
4. Build standardized payload:

```json
{
	"status": "ok|fail|exception",
	"message": {
		"code": "SUCCESS",
		"title": "Success",
		"description": "Operation completed successfully.",
		"category": "success"
	},
	"data": {}
}
```

5. Return DRF `Response` with mapped HTTP status.

### Exception-Aware Behavior

If `exception` is provided:

- In `DEBUG=True`, traceback is printed with exception name appended to description.
- In `DEBUG=False`, traceback is logged via module logger.

This keeps client contract stable while preserving diagnostics for operators.

### Input Contract Enforcement

`json_response(...)` is type-enforced (`typeguard`) for key parameters:

- `code` must be `str`.
- `category` must be one of: `danger`, `warning`, `info`, `success`.
- `data` must be `dict` or `list[dict]`.

Invalid category/value types raise type-check errors before response assembly.

## Non-JSON Response Paths

`MindoffResponseHandler` also supports transport-specific responses:

- **`file_response(...)`**: returns `FileResponse` from file path or `BytesIO`; infers content type and sets download filename. On failure, returns JSON fallback via `UNEXPECTED_ERR`.
- **`text_response(...)`**: returns plain text `HttpResponse`.
- **`html_response(...)`**: returns HTML `HttpResponse`.

These methods allow non-JSON payload delivery without bypassing the toolkit.

## Contract Guarantees

Response Kit is designed to guarantee:

- Stable envelope shape for JSON responses.
- Centralized mapping from response code to HTTP status/title/description.
- Predictable fallback path when invalid codes are used.
- Shared behavior across all APIs that return through `mo_response_kit`.

## Integration With Other Runtime Layers

Response Kit is directly coupled to API runtime and queue runtime:

- **API Kit (`MindoffAPIMixin`)** uses `mo_response_kit.json_response(...)` as the normalized response surface for direct and queue entry flows.
- **Queue process internals** read `MINDOFF_RESPONSES` to convert response codes into persisted numeric `response_code` values.
- **Exception normalization paths** rely on consistent response code metadata to preserve client-visible contract across failures.

This coupling is intentional: one response dictionary governs both immediate API responses and queued task result metadata.

## Exception Logging Architecture

Exception handling is designed to keep client contracts stable while preserving internal debugging depth.

### Internal Flow

1. Exception is raised in validation, API execution, or queue worker path.
2. Runtime captures traceback and execution context.
3. Exception details are logged internally (or printed in debug mode).
4. Client receives normalized response envelope through `mo_response_kit`.
5. Queue mode persists failure detail with task status/response metadata for later inspection.

### Recommended Logging Fields

- `api_url_name`
- request method and route
- queue/task UUID (for queue mode)
- authenticated user reference (if present)
- response code and HTTP status
- exception class and traceback
- correlation/request ID (if configured)

### Production Safety

- Redact secrets and source internals before writing logs.
- Keep verbose traces in internal logs, not in client responses.
- Prefer structured logs to support search, dashboards, and alerting.

## Operational Caveats

- `json_response` accepts only `2xx`, `4xx`, and `5xx` mapped statuses for envelope normalization. Codes mapping to other status classes (for example `3xx`) will raise validation errors during status derivation.
- `file_response` catches runtime errors and downgrades to JSON `UNEXPECTED_ERR`, which changes response type by design during failures.
- Response quality depends on `responses.csv` integrity; malformed dictionaries fail at load time.
- `json_response` fallback for unknown codes always resolves through `UNEXPECTED_ERR`, so missing custom codes silently collapse to a generic server-error envelope.
- In debug mode, traceback is printed to stdout; in non-debug mode, traceback is logged. Operational tooling should monitor the logger path in production.

## Troubleshooting the Kit

1. **Unknown response code fallback appears:** verify code exists in `config/responses.csv`.
2. **Startup/load errors for responses:** verify required headers and non-empty required fields.
3. **Unexpected envelope `status`:** verify `http_status` mapping for that code.
4. **File downloads returning JSON errors:** inspect file path/stream handling and logged exception output.
5. **Inconsistent messages across endpoints:** remove hardcoded strings and rely on centralized response codes.
6. **Queue rows show unexpected `response_code`:** verify response code mapping in `responses.csv` and ensure startup load happened in worker process.
