# Responses

Define stable response contracts so frontend and integrators can rely on one predictable envelope.

## Prerequisites

- [API Class](api-development.md) scaffolded and routed.
- `config/responses.csv` exists in the project.

## Implementation

Below are practical examples and usage instructions for each response helper function in the Response Kit. These functions create standardized JSON responses with consistent status codes, messages, and data envelopes across Django Rest API endpoints.

{{ MO_RESPONSE_KIT_FUNCTIONS }}

## Core Concepts

### 1. Standard JSON Envelope

`json_response(...)` returns a consistent envelope:

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

Envelope fields:

- `status`: Derived from response code HTTP status (`2xx -> ok`, `4xx -> fail`, `5xx -> exception`).
- `message.code`: Stable response code from `responses.csv`.
- `message.title` and `message.description`: Human-readable values loaded from `responses.csv`.
- `message.category`: One of `danger`, `warning`, `info`, `success`.
- `data`: Payload body (object/list; defaults to `[]` when empty).

### 2. Response Codes

Response code definitions are loaded from:

```text
config/responses.csv
```

Required headers:

- `code`
- `title`
- `description`
- `http_status`

Practical rules:

- `code` is normalized to uppercase.
- Duplicate codes are rejected during load.
- `http_status` must be within `200..599`.
- If a code is missing/unknown, the handler falls back to `UNEXPECTED_ERR`.

<div class="admonition warning">
<p class="admonition-title">Keep response codes centralized</p>
<p>The django-mindoff project ships with a standard set of response codes. Do not remove or alter the default codes as they are used by Mindoff's Core features. However, feel free to update their title, description, and http_status as the developer feels fit for the project.</p>
<p>Do not hardcode title/description in endpoint code. Add or update codes in <code>config/responses.csv</code> so behavior stays consistent across APIs.</p>
</div>

## Troubleshooting

- `Unknown Response code: '<CODE>'`  
  Add the code to `config/responses.csv` or use an existing code.
- Startup/load failures for responses file  
  Ensure `config/responses.csv` exists and includes required headers.
- Unexpected `status` in JSON envelope  
  Verify `http_status` configured for the code in `responses.csv`.
