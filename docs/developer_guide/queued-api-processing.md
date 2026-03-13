# Queue Mode in API

Queue mode is for long-running API work that should not block request/response.
This guide shows how to run APIs asynchronously with Redis and Dramatiq.
For architecture internals, see [API Kit](../architecture/api-kit.md#queue-mode-asynchronous).
Use this mode when endpoint runtime is high and clients should poll or stream progress instead of waiting for a direct response.

## Prerequisites

- `redis-server` installed locally or via docker
- `MOQueue` model successfully migrated to DB under `tbl_mo_queue`.

### 1. Start Redis

Use one of the following:

```bash
# Local Redis (if installed)
redis-server
```

If you use Docker, make sure Docker is running.

```bash
# Docker
docker run --name mindoff-redis -p 6379:6379 redis:7
```

### 2. Ensure `REDIS_URL` Is Set

`REDIS_URL` is created during initialization and should be present in `.env` and loaded in `config/settings.py`:

```env
REDIS_URL=redis://127.0.0.1:6379/0
```

```python
REDIS_URL = config("REDIS_URL", default=None)
```

### 3. Start the Dramatiq Worker

From the same virtual environment as your project:

```bash
dramatiq django_mindoff.components._api_kit.queue_process
```

## Implementation

### 1. Mindoff Queue API Class

Once Redis and the worker are running, queue mode is enabled by setting `process_mode = "queue"` on the API class.

<div class="admonition warning">
<p class="admonition-title">Queue mode does not support file uploads</p>
<p>Multipart or file uploads are blocked when <code>process_mode="queue"</code>.</p>
</div>

**Example queue-mode API configuration:**

```python
class CreateOrderV1APIView(MindoffAPIMixin):
    api_url_name = "orders__create_order"
    api_name = "Create Order"
    api_description = "Create a new order."
    method = "post"

    process_mode = "queue"
    allow_duplicate_queue = False
    progress_steps = {
        "validate": {"label": "Validated input", "percent": 10},
        "process": {"label": "Processed data", "percent": 60},
        "finalize": {"label": "Finalized response", "percent": 90},
    }
```

**Queue-Specific API Attributes**

These attributes only apply to queue-mode APIs. For the complete API attribute list, see API Development's [Mindoff API Class](api-development.md#2-mindoff-api-class).

| Attribute                       | Purpose                                                   | Typical values    |
| ------------------------------- | --------------------------------------------------------- | ----------------- |
| `process_mode`                  | Enable queue execution.                                   | `queue`           |
| `allow_duplicate_queue`         | Allow multiple queued jobs per user with identical input. | `True` or `False` |
| `progress_steps`                | Defines progress checkpoints and their percentages.       | dict or `None`    |
| `queue_detail_api_limit`        | Rate limit for queue detail endpoint.                     | `30/m`            |
| `queue_status_stream_api_limit` | Max concurrent status streams.                            | `3`               |
| `queue_cancel_api_limit`        | Rate limit for cancel endpoint.                           | `30/m`            |
| `queue_retry_api_limit`         | Rate limit for retry endpoint.                            | `30/m`            |

### 2. Progress Checkpoints

{{ MO_API_PROGRESS_CHECKPOINT }}

## Core Concepts

### 1. Queue Response Format

When an API runs in queue mode, the initial response is a queued acknowledgment:

```json
{
	"status": "ok",
	"message": {
		"code": "QUEUED",
		"title": "Queued",
		"description": "Operation queued successfully.",
		"category": "success"
	},
	"data": {
		"queue_id": "<uuid>",
		"response_url": "<absolute-url>",
		"status_stream_url": "<absolute-url>",
		"cancel_url": "<absolute-url>",
		"retry_url": "<absolute-url>",
		"progress_steps": {}
	}
}
```

Use `response_url` to fetch the final result, or `status_stream_url` for live progress updates.
`progress_steps` is echoed back so clients can display expected checkpoints.

### 2. Built-in APIs to Manage Queue

Once a task is queued, use these endpoints to retrieve results, monitor status, or control execution:

| Endpoint                               | Method | Purpose                                                  | Notes                                        |
| -------------------------------------- | ------ | -------------------------------------------------------- | -------------------------------------------- |
| `queue/<uuid:queue_task_uuid>/`        | `GET`  | Retrieve the result (or current status) of a queue task. | Returns final response when completed.       |
| `queue/list/`                          | `GET`  | List queue tasks with optional filtering.                | Query params below.                          |
| `queue/<uuid:queue_task_uuid>/stream/` | `GET`  | Real-time queue task status via SSE.                     | `text/event-stream`.                         |
| `queue/<uuid:queue_task_uuid>/cancel/` | `POST` | Cancel a running or queued task.                         | Returns cancel requested or not cancellable. |
| `queue/<uuid:queue_task_uuid>/retry/`  | `POST` | Retry a previously failed queue task.                    | Returns new queued response.                 |

Queue list query params (all optional, combinable):

- `id`
- `job_status`
- `user_ref_id`
- `owner_id`
- `api_url_name`
- `page`
- `page_size`

### 3. MOQueue Model and Task Persistence

Queue tasks are persisted in the `MOQueue` model. This provides durable storage for:

- `job_status`
- request snapshot
- response payload and HTTP status
- error payload (if failed)

Redis stores live progress for running tasks and feeds the SSE stream. The queue detail endpoint reads from the database, while the status stream reads live state from Redis.

## Troubleshooting

- Redis-related errors in queue mode  
  Set `REDIS_URL` correctly and ensure Redis is running locally or reachable from your host.
- `MOQueue` table missing or errors about `tbl_mo_queue`  
  Run:
  ```bash
  python manage.py makemigrations django_mindoff
  python manage.py migrate
  ```
