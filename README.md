<h1>Django Mindoff</h1>

_Build production-ready Django REST APIs faster with less boilerplate._

![Django Mindoff cover image](https://raw.githubusercontent.com/mindoffwork/mindoff.work/refs/heads/root/public/images/projects/django_mindoff/django-mindoff-cover-with-name.png)

Django Mindoff is an architectural framework that manages the structure and mechanics of API development so developers can focus on business logic, with efficient data workflows powered by Polars.

[![Coverage Status](https://codecov.io/gh/mindoffwork/django-mindoff/branch/root/graph/badge.svg)](https://codecov.io/gh/mindoffwork/django-mindoff)
[![PyPI version](https://img.shields.io/pypi/v/django-mindoff.svg?logo=pypi&logoColor=white)](https://pypi.org/project/django-mindoff/)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://github.com/mindoffwork/django-mindoff/actions/workflows/ci.yml)

**Documentation**: [https://django.mindoff.work](https://django.mindoff.work)

**Source**: [https://github.com/mindoffwork/django-mindoff](https://github.com/mindoffwork/django-mindoff)

**Case Study**: [https://mindoff.work/projects/django-mindoff](https://mindoff.work/projects/django-mindoff/)

## Philosophy

Django Mindoff is not a replacement for Django or Django REST Framework — it is a purposeful complement built on top of both.

Django and DRF handle the things they were designed for extremely well: request routing, authentication, permissions, serializer-driven row-level validation, and the request/response lifecycle. Mindoff uses Django and DRF internally for exactly that and does not compete with them. What they were not designed for is **high-volume tabular data**: fetching 100 000 rows through a serializer loop, bulk-inserting a Polars DataFrame with FK integrity checks across related models, or streaming a dataset that exceeds available RAM without ever holding it all in memory.

Mindoff fills that gap with a data-engineering layer built around **Polars DataFrames and LazyFrames**. The rule of thumb is simple:

- **For everyday operations** — creating a single record, updating a user profile, listing 20 results — native Django serializers or pandas are the right tools. They are simpler to write, easier to debug, and perfectly adequate at that scale.
- **For large dataset workflows** — bulk ingestion from external sources, high-volume exports, data transformation pipelines, update-or-insert across tens of thousands of rows — use `mo_crud_kit`. It applies the same structured, model-aware validation as a serializer, but runs it in a vectorized, loop-free pipeline and writes to the database using each backend's native bulk-load path rather than row-by-row SQL.

The rest of the framework — structured APIs, validation helpers, response contracts, background queueing, test utilities — is designed to work naturally alongside standard Django/DRF patterns, not replace them.

## Key Features

1. **Project Setup That Just Works**  
   Start a new API project with guided CLI commands for init, create, delete, and nuke. Projects start ready to run with a sensible structure, so developers can begin building APIs immediately without worrying about project layout.

2. **Fully Managed APIs**  
   Define request method, access control, payload rules, rate limits, and execution mode in a single API definition. Mindoff enforces these rules automatically, handling validation, security checks, and execution flow behind the scenes.

3. **API Versioning That Stays Manageable**  
   Ship and evolve versioned APIs using a built‑in routing structure. APIs are automatically organized so new versions stay clean while existing clients continue working without disruption.

4. **Queue-Ready APIs**  
   Run APIs synchronously or as background processes when needed. Simply switch `process_mode` to `"queue"` and Mindoff handles queue orchestration, status tracking, progress updates, cancellation, and retries.

5. **Validation in One Line**  
   Use simple validation helpers that keep API logic clean and cognitively light. With aggregation support, multiple validation errors can be captured together and returned in a structured response.

6. **Consistent Responses, Every Time**  
   Return responses through a unified response structure using response endpoints. Messages stay professional, consistent, and predictable across the entire API surface.

7. **Vectorized Model Validation & Bulk Writes**  
   Pass a `model_frame` (DataFrame or LazyFrame) to `create` or `update` and Mindoff validates it automatically against the Django model. Validation runs in a vectorized, loop‑free pipeline and valid rows are written directly to the model's table with high efficiency.

8. **Querysets to DataFrames, Instantly**  
   Provide a Django queryset and Mindoff converts the results into a DataFrame or LazyFrame. Data is returned with built‑in pagination and streaming support, making large reads predictable and efficient.

9. **Optimized Polars Utilities for DataFrame & LazyFrame**  
   Run checks, conversions, and transformations seamlessly across both DataFrame and LazyFrame with Mindoff’s Polars utilities. Operations run natively in vectorized form and stay tuned for performance and efficiency, keeping data pipelines smooth and predictable.

10. **Tests With Almost No Setup**  
    Write API tests using declarative test mixins that talk to the API automatically. Focus on verifying behavior instead of crafting request calls and basic assertions, which are handled automatically by Mindoff.

## Quick Start

### 1. Install the Package

```bash
pip install django-mindoff
```

### 2. Initialize a Project

```bash
django-mindoff init
```

_This sets up the project foundation for you, including structure, config files, and ready-to-run wiring. Framework dependencies are managed internally, and `django-mindoff` aligns compatible Django, DRF, and Polars versions for you. Read the complete list here: [requirements guide][requirements-guide]._

What you should see:

- A new project scaffold with `manage.py`, `mindoff.py`, `config/`, and `apps/`
- Environment and config files ready to use

### 3. Create an App

```bash
python mindoff.py create
```

In the interactive flow, choose option 1 and create an app named `shop`.

What you should see:

- App folder at `apps/shop/`
- App route linked as versioned URL at `config/urls.py`
- App path added `INSTALLED_APPS` at `config/settings.py`

### 4. Create an API

```bash
python mindoff.py create
```

In the interactive flow, choose option 2 and create an API named `ping` under the `shop` app.

_This generates the API file and wires its route so the endpoint is callable right away._

What you should see:

- API file at `apps/shop/apis/ping.py`
- URL route auto-registered at `apps/shop/urls.py`

### 5. Return a Success Response

Open `apps/shop/apis/ping.py` and add this inside the API class `run()` method:

```python
def run(self, request, *args, **kwargs):
    return mo_response_kit.json_response(
        code="SUCCESS",
        category="success",
        data={"message": "Hello from shop ping"}
    )
```

### 6. Run and Verify

🔔 Before running, make sure your project's virtual environment is active.

Run the Migrations:

```bash
python manage.py makemigrations
python manage.py migrate
```

Run the Local Server:

```bash
python manage.py runserver
```

Open:

- `http://127.0.0.1:8000/v1/shop/ping/`

**What you should get:**

- A JSON response with status, structured message metadata, and your data.message

✅ You now have a working API endpoint running with a structured success response.

From here, shape the `run()` method around your real business logic and output. To control response messaging, add custom entries in `config/responses.csv` with your preferred `http_status` code.

When you are ready to move beyond Quick Start, continue with the [developer guide][developer-guide]. It covers the package features in detail, explains configuration and architecture choices, and helps you build real-world applications with confidence.

## License

This project uses the same BSD 3-Clause License as the Django project. See the [LICENSE][project-license] file for full terms.

[requirements-guide]: https://django.mindoff.work/latest-release/architecture/management-kit/#default-package-set-installed-by-init
[developer-guide]: https://django.mindoff.work/latest-release/developer_guide/
[project-license]: https://github.com/mindoffwork/django-mindoff/blob/root/LICENSE
