# django-mindoff

`django-mindoff` is an architectural framework designed to eliminate repetitive API setup so developers can focus on business logic instead of infrastructure, scaffolding, and version coordination.

It combines project automation, standardized API architecture, structured responses, testing utilities, and Polars-powered workflows into one cohesive system.

## Project Status 🧪

`django-mindoff` is currently experimental and in active development. Architecture, philosophy, functionality and structure may evolve. It is designed for fresh Django-Mindoff projects and is not intended for retrofitting into existing Django applications.

## Requirements ⚙️

- Python ≥ 3.12

All other framework dependencies are managed internally. `django-mindoff` selects compatible versions of Django, DRF, and Polars so you do not have to manage version alignment manually.

## Installation 📦

```bash
pip install django-mindoff
```

## Quick Start 🚀

### 1. Initialize a Project

```bash
django-mindoff init
```

Creates a fully structured Django-Mindoff project with:

- Virtual environment
- `.env` configuration
- Git initialization (if available)
- Preconfigured architecture

### 2. Create Apps, Models, and APIs

```bash
python mindoff.py create
```

Interactive CLI allows you to:

- Create apps → `apps/<app_name>/`
- Create models → `apps/<app_name>/models.py`
- Create APIs → `apps/<app_name>/apis/<api_name>.py`
- Auto-register routes

### 3. Add Your Logic

Edit your generated API and return structured responses:

```python
return mo_response_kit.json_response(
    code="SUCCESS",
    category="success",
    data={"message": "Hello World"}
)
```

Run the server. Focus on your logic. Repeat.

## Response Standard 📄

All responses follow a consistent structure:

```json
{
	"status": "ok",
	"message": {
		"code": "SUCCESS",
		"title": "Success",
		"description": "Operation completed successfully.",
		"category": "success"
	},
	"data": {
		"id": "ec4786f7-3646-4159-a091-7c83ba3addaf",
		"name": "John Doe"
	}
}
```

No unstructured exceptions. No inconsistent payloads.
Umm... One more thing. `django-mindoff` uses uuids for primary keys and foreign keys by design.

## Architecture 🧭

### Versioned Routing

```
<int:version>/<app_name>
```

Routers resolve the correct API version automatically.

### Project Structure 📁

A freshly generated project follows this layout:

```text
project_root/
├─ manage.py
├─ mindoff.py
├─ pytest.ini
├─ README.md
├─ .env
│
├─ config/
│  ├─ settings.py
│  ├─ urls.py
│  ├─ asgi.py
│  ├─ wsgi.py
│  └─ responses.csv
│
├─ templates/
│  ├─ index.html
│  └─ 404.html
│
└─ apps/
   └─ <app_name>/
      ├─ apps.py
      ├─ models.py
      ├─ serializers.py
      ├─ views.py
      ├─ urls.py
      ├─ apis/
      │  └─ <api_name>.py
      ├─ components/
      │  └─ <component_name>.py
      └─ tests/
         ├─ test_views.py
         └─ test_apis/
            └─ test_<api_name>.py
```

## Core Kits 🧰

Mindoff is modular. Each kit removes a specific category of friction.

**1. Project Management Kit:**
Prompt-driven CLI for creating and organizing projects, apps, models, and APIs.

**2. API Kit:**
Centralized configuration layer handling validation, security, documentation, and error handling.

**3. CRUD Kit:**
Polars `DataFrame` and `LazyFrame` driven database operations with validation.

**4. Polars Kit:**
High-performance utilities for streaming-safe frame operations.

**5. Validation Kit:**
Single-line validation helpers that reduce conditional complexity.

**6. Response Kit:**
Structured JSON, file, text, and HTML responses.

**7. TDD Kit:**
Testing base classes and helpers for rapid API testing.

## CLI Overview 🛠️

```bash
django-mindoff init      # Create project
django-mindoff delete    # Delete project
python mindoff.py create # Create app/model/api
python mindoff.py delete # Delete app
```

## Testing ✅

`MindoffTestCase` provides:

- Pytest integration
- Mock app and model generation
- Polars DataFrame and LazyFrame support
- Assertion helpers

## Configuration ⚙️

Optional settings in `settings.py`:

```python
MINDOFF_LOG_ERRORS_IN_DEBUG = False
MINDOFF_TRACEBACK_DIRS = ["apps", "config"]
REDIS_URL = config("REDIS_URL")
POLARS_VALIDATOR_ERROR_COL = "__error__info"
MINDOFF_USE_VIEW_CACHE = False
```

`.env` is auto-generated during initialization.
