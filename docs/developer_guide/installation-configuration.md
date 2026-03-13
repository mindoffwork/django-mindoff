# Installation & Configuration

Let's start from a clean baseline and get a project running with the right defaults. `django-mindoff` provides a structured foundation for building APIs with predictable scaffolding and consistent runtime conventions.
By following these steps, you'll have a fully configured project ready for development.

## Prerequisites

- Python `{{ PYTHON_REQUIRES }}`
- `pip`
- `git`
- An empty or dedicated project directory

## Implementation

### 1. Install django-mindoff

```bash
pip install django-mindoff
```

The installation exposes the CLI:

```bash
django-mindoff --help
```

### 2. Initialize a New Project

From your project directory:

```bash
django-mindoff init
```

<div class="admonition info">
<p class="admonition-title">Heads up!</p>
<p><code>django-mindoff init</code> sets up a virtual environment, git and installs the framework's compatible dependency set automatically. This includes core packages such as Django, Django REST Framework, Polars, and supporting runtime/testing dependencies required by the scaffold.</p>
<p>For full dependency details, see <a href="../architecture-requirements.md">Architecture Requirements</a>.</p>
</div>

<div class="admonition warning">
<p class="admonition-title">This may take a few minutes</p>
<p><code>django-mindoff init</code> can take some time to complete depending on internet speed and system capabilities.</p>
</div>

The command scaffolds a runnable project, including:

- `manage.py` -- Django management entry point.
- `mindoff.py` -- Local CLI entry point for app/model/API scaffolding tasks.
- `.env` -- Environment configuration file generated for local setup.
- `pytest.ini` -- Base test runner configuration.
- `README.md` -- Project-level readme scaffold.
- `config/` -- Core Django configuration package. Typically includes: `settings.py`, `urls.py`, `asgi.py`, `wsgi.py`, and `responses.csv`.
- `apps/` -- Workspace for all domain apps we create with the CLI.
- `templates/` -- Base HTML templates created by the scaffold.

For the full directory tree and architecture-level breakdown, see
[Management Kit](../architecture/management-kit.md).

### 3. Configure Core Settings

Open `config/settings.py` and set core defaults for local development:

```python
DEBUG = True
ALLOWED_HOSTS = []
```

If queue/background processing is needed, set Redis in `.env` ( see
[Queue Mode API](queued-api-processing.md) for more details.):

```env
REDIS_URL=redis://127.0.0.1:6379/0
```

Then load it in `config/settings.py` (if not already configured):

```python
REDIS_URL = config("REDIS_URL", default=None)
```

### 4. Run Virtual Environment

Before running migrations or starting the server, activate the project's virtual environment.

Windows (Command Prompt):

```bat
.venv\Scripts\activate.bat
```

Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 5. Run the Project

Run initial migrations to prepare your database:

```bash
python manage.py makemigrations
python manage.py migrate
```

Start the server:

```bash
python manage.py runserver
```

If the server starts without configuration errors, setup is complete.
Continue to [App and Model Setup](app-and-model-setup.md).

## Troubleshooting

- `django-mindoff: command not found`  
  Install with the same Python interpreter you use for the project environment.
- `ModuleNotFoundError` during startup  
  Activate your virtual environment, then reinstall dependencies.
