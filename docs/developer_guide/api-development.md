# API Development

Build maintainable, scalable, and class-based DRF endpoints with predictable routing, validation, and response contracts.

## Prerequisites

- [Django Mindoff Project](installation-configuration.md).
- [A Django App](app-and-model-setup.md).
- [An Active Virtual environment](installation-configuration.md#4-run-virtual-environment).

## Implementation

### 1. Create an API

Run:

```bash
python mindoff.py create
```

In the interactive menu, select:

- `2` for `api`

Then:

1. Select the target app from the list.
2. Enter API name in snake_case, for example: `create_order`
3. Optionally enter custom URL patterns, or leave blank to auto-generate route paths.

**What the CLI Generates for Each API**

`python mindoff.py create` (option `2`) generates and wires multiple pieces:

1. Generated output goes to `apps/<app_name>/apis/<api_name>.py`.
2. Creates/updates API class file: `apps/<app_name>/apis/<api_name>.py`.
3. Creates/updates version router wiring in `apps/<app_name>/views.py`.
4. Registers endpoint route in `apps/<app_name>/urls.py`.
5. Creates API tests in `apps/<app_name>/tests/test_apis/test_<api_name>.py`.
6. Creates/updates router tests in `apps/<app_name>/tests/test_views.py`.

**Where APIs Live**

Each API is a class-based view placed under the app's `apis/` folder:

```text
apps/<app_name>/apis/<api_name>.py
```

### 2. Mindoff API Class

The generated class is a `MindoffAPIMixin` subclass that control API identity, access, execution mode, request validation limits, and per-user usage limits.

After you create an API, your `apps/<app_name>/apis/<api_name>.py` file will include a class like this with the class name, `api_url_name`, and `api_name` already filled in for your API.
Override only the attributes you need for your endpoint behavior.

```python
--8<-- "apps/django_mindoff/components/managers/resources/api_class.txt"
```

**API Attribute Reference**

| Attribute                       | Purpose                                                     | Typical values                 |
| ------------------------------- | ----------------------------------------------------------- | ------------------------------ |
| `api_url_name`                  | Stable route name used in URL resolution and config checks. | `<app>__<api>`                 |
| `api_name`                      | Human-readable name for docs and logs.                      | `Create Order`                 |
| `api_description`               | Short description of the API.                               | `Create a new order.`          |
| `authentication_classes`        | DRF auth classes to apply.                                  | `[TokenAuthentication]`        |
| `permission_classes`            | DRF permissions to enforce.                                 | `[IsAuthenticated]`            |
| `method`                        | Allowed HTTP method for this API.                           | `get`, `post`, `put`, `delete` |
| `process_mode`                  | Execution mode.                                             | `direct`, `queue`              |
| `allow_duplicate_queue`         | Allow multiple queued jobs per user when in queue mode.     | `True` or `False`              |
| `progress_steps`                | Progress definition for queue mode.                         | dict or `None`                 |
| `payload_schema`                | Payload validation schema.                                  | dict, list, or `None`          |
| `max_payload_size`              | Max payload size in MB.                                     | `10`                           |
| `max_payload_depth`             | Max nesting depth allowed.                                  | `4`                            |
| `payload_validation`            | Validation strictness for payload schema.                   | `strict`, `basic`, or `None`   |
| `api_request_limit`             | Rate limit for the primary endpoint.                        | `30/m`                         |
| `queue_detail_api_limit`        | Rate limit for queue detail endpoint.                       | `30/m`                         |
| `queue_status_stream_api_limit` | Max concurrent status streams.                              | `3`                            |
| `queue_cancel_api_limit`        | Rate limit for queue cancel endpoint.                       | `30/m`                         |
| `queue_retry_api_limit`         | Rate limit for queue retry endpoint.                        | `30/m`                         |

Queue-specific attributes are documented in Queue Mode API - [Mindoff Queue API Class](queued-api-processing.md#1-mindoff-queue-api-class).

### 3. Example Usage

```python
from typing import Literal
from django_mindoff.components.api_kit import MindoffAPIMixin
from django_mindoff.components.response_kit import mo_response_kit


class CreateOrderV1APIView(MindoffAPIMixin):
    api_url_name = "orders__create_order"
    api_name = "Create Order"
    api_description = "Create a new order."
    method = "post"
    payload_schema = {
        "customer_id": str,
        "items": [
            {
                "sku": str,
                "qty": int,
            }
        ],
    }

    def run(self, request, *args, **kwargs):
        payload = request.data
        items = payload.get("items", [])
        total_qty = sum(item["qty"] for item in items)
        unique_skus = sorted({item["sku"] for item in items})
        return mo_response_kit.json_response(
            code="SUCCESS",
            category="success",
            data={
                "order_id": "generated-id",
                "total_qty": total_qty,
                "unique_skus": unique_skus,
            },
        )
```

`django-mindoff` automatically wires your app `urls.py` to the router created in `views.py`. For `create_order`, the generated entry looks like this:

```python
urlpatterns = [
    path("create_order/", create_order_router, name="orders__create_order"),
]
```

With the `create_order` example above, use the following settings and payload to test the API:

**Method:** `POST`  
**URL:** `http://localhost:8000/v1/orders/create_order/`  
**Headers:** `Content-Type: application/json`  
**Body (JSON):**

```json
{
	"customer_id": "cst_123",
	"items": [
		{ "sku": "SKU-001", "qty": 2 },
		{ "sku": "SKU-002", "qty": 1 }
	]
}
```

Alternatively, you can call it with curl:

```bash
curl -X POST http://localhost:8000/v1/orders/create_order/ \
  -H "Content-Type: application/json" \
  -d "{\"customer_id\":\"cst_123\",\"items\":[{\"sku\":\"SKU-001\",\"qty\":2},{\"sku\":\"SKU-002\",\"qty\":1}]}"
```

## Core Concepts

The following concepts influence how Mindoff APIs validate requests and enforce access rules.
Understanding them helps you design predictable endpoints.

### 1. Payload Validation

`payload_schema` defines the expected shape of request data and drives automatic validation.
It keeps payload checks consistent and moves structural validation out of endpoint logic.

**Supported `payload_schema` Types**

| Schema type    | Example                                | Notes                                       |
| -------------- | -------------------------------------- | ------------------------------------------- |
| Primitive type | `str`, `int`, `float`, `bool`          | Validates type directly.                    |
| Dict shorthand | `{"customer_id": str, "items": [...]}` | Keys are required in `strict` mode.         |
| List shorthand | `[{"sku": str, "qty": int}]`           | Use a single item schema.                   |
| Typed list     | `list[str]` or `List[str]`             | Validates each item in the list.            |
| Typed dict     | `dict[str, int]` or `Dict[str, int]`   | Validates keys and values by type.          |
| Union          | `Union[str, int]`                      | Accepts any one of the union types.         |
| Literal        | `Literal["A", "B"]`                    | Restricts values to a fixed set.            |
| Optional       | `Union[str, None]`                     | Allows `None` in addition to the base type. |

Validation behavior is controlled by `payload_validation`:

- `strict`: missing keys are validation errors
- `basic`: missing keys are ignored, but existing keys are validated
- `None`: skip schema validation

<div class="admonition info">
<p class="admonition-title">Extra keys are allowed</p>
<p><code>payload_schema</code> only validates keys it knows about; extra keys are accepted in all modes.</p>
</div>

Use `max_payload_size` and `max_payload_depth` to guard against large or deeply nested payloads.

### 2. Authentication

Use the same authentication and permission classes you would use in standard DRF APIs:

```python
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

authentication_classes = [TokenAuthentication]
permission_classes = [IsAuthenticated]
```

<div class="admonition warning">
<p class="admonition-title">Session authentication is not recommended</p>
<p><code>django-mindoff</code> is API-first. Prefer token-based authentication for API workflows; use session authentication only when your API design intentionally requires it.</p>
</div>

### 3. Queue Mode

Set `process_mode = "queue"` when the API should run asynchronously.

- Returns a queue acknowledgment with task identifiers.
- Enables status, cancel, retry, and stream endpoints.
- Supports `progress_steps` for progress reporting.

For detailed queue behavior, endpoints, and progress checkpoints, see [Queue Mode in API](queued-api-processing.md).

### 4. Versioning APIs

Each API is wrapped by a version router in `apps/<app_name>/views.py`:

```python
from django_mindoff.components.api_kit import mo_api_kit
from .apis.create_order import CreateOrderV1APIView


class CreateOrderRouter(mo_api_kit.APIVersionRouter):
    VERSION_MAP = {
        1: CreateOrderV1APIView,
    }


create_order_router = CreateOrderRouter()
```

Add new versions by creating a new API class and adding it to `VERSION_MAP`.

### 5. Responses and Codes

Return responses through `mo_response_kit` to keep a stable response envelope:

- `mo_response_kit.json_response(...)`
- `mo_response_kit.text_response(...)`
- `mo_response_kit.file_response(...)`

Response codes and messages live in `config/responses.csv`.
See [Responses](responses.md) for details.

### 6. Testing the API

When you create an API, `django-mindoff` generates a test file automatically under:

```text
apps/<app_name>/tests/test_apis/test_<api_name>.py
```

Add tests as you implement the endpoint, then move to [Test-Driven Development](test-driven-development.md).

### 7. Practical Conventions

- Prefer behavior-based names (`create_order`, `list_orders`, `cancel_order`).
- Keep each API focused on one responsibility.
- Confirm route registration before implementation.

## Troubleshooting

- `api_url_name not found in urls.py`  
  Ensure the route name matches the URL registration in `apps/<app_name>/urls.py`.
- `A valid method must be configured`  
  Set `method` to one of `get`, `post`, `put`, `delete`.
- Payload validation errors for missing keys  
  Switch `payload_validation` to `basic` if missing keys are optional.
