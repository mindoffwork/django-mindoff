# Test-Driven Development

Use TDD to keep API behavior stable as features evolve.

## Prerequisites

- [API Class](api-development.md) is scaffolded and routed.
- Expected response codes are defined using `response_kit` (see [Responses](responses.md)).

## Implementation

When you [Create an API](api-development.md#1-create-an-api) using the command `python mindoff.py create`, `django-mindoff` generates test scaffolding automatically:

1. API behavior test: `apps/<app_name>/tests/test_apis/test_<api_name>.py`
2. Router/version test: `apps/<app_name>/tests/test_views.py`

Use this scaffolding as your baseline and expand test coverage as endpoint logic evolves.

**What the CLI Generates for Each API**

`python mindoff.py create` (option `2` for `api`) wires test artifacts together:

1. Creates/updates API tests in `apps/<app_name>/tests/test_apis/test_<api_name>.py`.
2. Creates/updates router tests in `apps/<app_name>/tests/test_views.py`.
3. Keeps test names aligned with generated route names in `urls.py`.

**Where Tests Live**

Mindoff API tests are organized by purpose:

- Endpoint behavior tests in `apps/<app_name>/tests/test_apis/`
- Router/version tests in `apps/<app_name>/tests/test_views.py`

Router tests are usually maintenance-free unless you customize version routing.
Most of your TDD work happens inside `test_apis/test_<api_name>.py`.

### 1. Mindoff API Test Class

The generated class extends `MindoffTestCase` and is configured with core test inputs: identity, request parameters, and assertions. These inputs control API route identification, request setup, and response validation.

After you create an API, your `apps/<app_name>/tests/test_apis/test_<api_name>.py` file will include a class like this with the class name and `api_url_name` already filled in for your API.
Override only the attributes and add test methods as needed for your endpoint behavior.

```python
--8<-- "apps/django_mindoff/components/managers/resources/test_api_class.txt"
```

**Core Test Inputs**

| Input                    | Purpose                                       | Typical values                              |
| ------------------------ | --------------------------------------------- | ------------------------------------------- |
| `api_url_name`           | Route identifier used by test helpers.        | `<app>__<api>`                              |
| `payload`                | Request body for `POST`/`PUT` APIs.           | dict                                        |
| `query_params`           | Query string values for `GET` or filters.     | dict or `None`                              |
| `headers`                | Request headers, auth, custom metadata.       | dict or `None`                              |
| `url_kwargs`             | URL kwargs such as version segments.          | `{"version": 1}`                            |
| `expected_status_code`   | HTTP status assertion target.                 | `200`, `400`, `401`                         |
| `expected_response_type` | Response envelope type assertion.             | `json`, `plain`, `html`, `binary`, `others` |
| `is_queue_response`      | Queue-mode acknowledgment vs direct response. | `True` or `False`                           |

For direct APIs, set `is_queue_response=False`.
For queue-mode APIs, set `is_queue_response=True`.

### 2. Example Usage

The key is consistent use of the same `api_url_name` in both `mo_mock_call_api` and `mo_assert_api_response`.

```python
@pytest.mark.django_db(transaction=True)
class TestCreateOrderAPIView(MindoffTestCase):
    api_url_name = "orders__create_order" # Same as API Class

    def test_acceptance_api_success(self):
        user = self.mo_mock_user()
        payload = {
            "customer_id": "cst_123",
            "items": [
                {"sku": "SKU-001", "qty": 2},
                {"sku": "SKU-002", "qty": 1},
            ],
        }
        url_kwargs = {"version": 1}
        expected_status_code = 200
        expected_response_type = "json"

        response = self.mo_mock_call_api(
            self.api_url_name,
            user=user,
            payload=payload,
            url_kwargs=url_kwargs,
            is_queue_response=False,
        )
        self.mo_assert_api_response(
            api_url_name=self.api_url_name,
            response=response,
            expected_status_code=expected_status_code,
            expected_response_type=expected_response_type,
        )
```

### 3. Running Tests

From the project root:

```bash
pytest
```

Run only one API test file while iterating:

```bash
pytest apps/<app_name>/tests/test_apis/test_<api_name>.py -q
```

Run only router tests:

```bash
pytest apps/<app_name>/tests/test_views.py -q
```

## Core Concepts

`MindoffTestCase` provides reusable helpers to keep tests readable and fast, part of which is used by the Mindoff API Test Classes

{{ MINDOFF_TESTCASE_HELPERS }}

`MindoffRouterTestCase` is available for explicit router/version assertions in `test_views.py`.

### Practical TDD Loop

1. Write one behavior-focused test in `test_<api_name>.py`.
2. Arrange fixtures with `mo_mock_user()` and model helpers as needed.
3. Call the endpoint using `mo_mock_call_api(...)`.
4. Assert envelope and status with `mo_assert_api_response(...)`.
5. Add endpoint-specific assertions for response `data`, side effects, and error branches.
6. Use `pytest.mark.parametrize` for similar scenarios to avoid repetitive test code and keep test files concise.
7. Repeat for edge cases: invalid payload, missing auth, and boundary limits.

## Troubleshooting

- `NoReverseMatch` for `api_url_name`  
  Confirm `api_url_name` matches the route name in `apps/<app_name>/urls.py`.
- Test expects direct response but receives queue payload  
  Verify `is_queue_response` matches API `process_mode`.
- Failing auth/permission assertions  
  Ensure test user and headers align with API `authentication_classes` and `permission_classes`.
- Version routing failures in `test_views.py`  
  Check `VERSION_MAP` entries in `apps/<app_name>/views.py`.
