# Management Kit

The Management Kit is the part of `django-mindoff` that edits your project on disk. Runtime components handle requests and queue jobs. This layer handles the repository itself.

It creates and updates apps, APIs, models, settings, and routes so the project keeps a consistent structure as it grows.

For guided command usage, see:

- [Developer Guide - Installation & Configuration](../developer_guide/installation-configuration.md)
- [Developer Guide - App and Model Setup](../developer_guide/app-and-model-setup.md)
- [Developer Guide - API Development](../developer_guide/api-development.md)

## Architectural Philosophy

The core idea is straightforward:

- Separate command orchestration from file mutation.
- The command layer should stay readable and easy to extend.
- The mutation layer should stay deterministic and safe to run repeatedly.

### The Orchestrator-Mutator Split

| Component         | Responsibility                                                                                 | Examples                            |
| ----------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Orchestrators** | Decide _what_ should happen. Manage CLI arguments, prompts, and flow.                          | `init.py`, `create.py`, `delete.py` |
| **Mutators**      | Decide _how_ files change. Perform idempotent writes, patching, and template-based generation. | `_create_api.py`, `_create_app.py`  |

## Command Discovery & Extension

The CLI is module-driven. `mindoff.py` discovers manager modules from `django_mindoff.components.managers` instead of using one large command switch.

- **Participation:** A module is included only if it exposes `register_subcommand(subparsers)`.
- **Discovery rules:** Package modules are skipped, and `init`/`nuke` are explicitly excluded by the generated `mindoff.py` discovery loop.
- **Practical result:** You can add new management capability by adding a manager module, not by rewriting central CLI logic.

## Interactive vs Direct Usage

You can use the toolkit in two ways:

1. Interactive orchestration (`create`, `delete`) for guided workflows.
2. Direct command invocation (`createapp`, `createapi`, `createmodel`, `create_model_field`, `deleteapp`) for experienced users, comfortable with exact CLI inputs.

### Direct Command Reference

These commands can be called directly via `python mindoff.py <command> ...`.

| Command              | Purpose                                      | Required args                      | Optional args            | Notes                                                        |
| -------------------- | -------------------------------------------- | ---------------------------------- | ------------------------ | ------------------------------------------------------------ |
| `createapp`          | Create one or more Django apps under `apps/` | `app_names` (`nargs="+"`)          | None                     | Uses Django `manage.py startapp` under the hood.             |
| `createapi`          | Create API class + router + URL + tests      | `api_path` (`<app>/<api_name>`)    | `--url <pattern...>`     | Default URL becomes `<api_name>/` if omitted.                |
| `createmodel`        | Create model + serializer stubs              | `model_path` (`<app>/<ModelName>`) | None                     | Model name normalized to PascalCase + `Model`.               |
| `create_model_field` | Add a `ForeignKey` field to existing model   | `model_path`, `field_name`         | `--to <app>/<ModelName>` | `--to` is required at runtime for FK creation.               |
| `deleteapp`          | Delete one or more apps and unwind wiring    | `app_names` (`nargs="+"`)          | None                     | Removes app dir and related `settings.py`/`urls.py` entries. |

Examples:

- `python mindoff.py createapp billing`
- `python mindoff.py createmodel billing/Invoice`
- `python mindoff.py create_model_field billing/Invoice customer --to crm/Customer`
- `python mindoff.py createapi billing/create_invoice --url create/ create/<int:id>/`

## Key Lifecycles

### 1. The Initialization Pipeline

`init` is a full bootstrap flow, not just a thin wrapper around `startproject`.

1. **Environment:** Creates a virtual environment and installs managed dependencies.
2. **Scaffolding:** Generates the Django project and patches `settings.py` and `urls.py`.
3. **Resources:** Adds support files such as `mindoff.py`, `AGENTS.md`, `pytest.ini`, `.gitignore`, `.env`, HTML templates, and `responses.csv`.
4. **Version Control:** Initializes git to capture the generated baseline.

#### Default Package Set Installed by `init`

`init.py` installs dependencies from `pyproject.toml` into the new `.venv`.
The list below is auto-synced from code at docs build time:

{{ MANAGEMENT_INIT_REQUIREMENTS }}

`django-mindoff` is installed after the base list as a separate install step.

<div class="admonition tip">
<p class="admonition-title">Optional Packages</p>
<p><code>optional_packages</code> exists in the flow, but is currently empty unless extended. Optional packages are open to be extended for modders.</p>
</div>

#### Why Virtual Environment and Git Are Enforced

The default `init` behavior is opinionated on purpose:

- **Virtual environment from day 1:** ensures dependency isolation and reproducible runtime behavior.
- **Git initialization from day 1:** captures a clean baseline commit right after scaffolding, making later changes reviewable and rollback-friendly.

Together, this supports the intended outcome: production-minded API projects with stable environments, traceable changes, and predictable deployments.

#### Reliability: Built on Django CLI Under the Hood

Management Kit does not reimplement framework scaffolding primitives. It delegates core creation tasks to Django commands:

- Project creation uses `django-admin startproject config .`
- App creation uses `python manage.py startapp <app> <target_dir>`

This keeps long-term behavior aligned with Django internals and reduces drift risk across framework upgrades.

#### URL and Template Setup Performed by `init`

`init` configures first-run project UX and API entry wiring:

- Updates `config/urls.py` to include a root homepage route: `path("", TemplateView.as_view(template_name="index.html"))`.
- Updates `config/urls.py` to include Mindoff routes: `path("mindoff/", include(mindoff_urls))`.
- Updates `settings.py` template DIRS to include `templates/`
- Copies default templates (`index.html`, `404.html`) into project `templates/`

Operationally, this gives a custom homepage immediately and provides a project-level `404.html` template that Django can render when `DEBUG=False`.

### 2. The Creation Flow

`create.py` works as an interactive dispatcher. It collects user input, validates naming/selection rules, and forwards execution to mutator commands via subprocess calls.

For direct command usage and arguments, see [Direct Command Reference](#direct-command-reference).

### 3. The Delete App Flow

Delete is also split into orchestrator + mutator stages:

1. `delete.py` runs an interactive app selection flow (`delete` command).
2. It forwards selected apps to `deleteapp` via subprocess.
3. `_delete_app.py` (`DjangoAppDeleter`) performs per-app deletion with confirmation.
4. After filesystem deletion, it unwinds project wiring by removing the app from `INSTALLED_APPS`, removing the versioned include from project `urls.py`, and cleaning empty parent namespace directories when applicable.

This makes app removal structured and predictable instead of a manual folder delete.

### 4. The Nuke Flow

`nuke.py` handles project-level teardown with explicit safety controls:

1. Prompts for scope: **Basic** removes core generated artifacts while preserving defaults like `.git` and `.venv`; **Full** removes all listed artifacts, including `.git` and `.venv`.
2. Builds a concrete target list from known artifact paths.
3. Supports `--dry-run` preview before deleting anything.
4. Requires explicit confirmation before destructive execution.
5. Deletes targets and prints deleted/skipped summary.

`nuke` is intentionally explicit and interactive because it operates at repository scope.

## Specialized Mutators

Mutators are designed around idempotency. Running an operation twice should not leave the project in a broken state.

### App & API Generation

- **`_create_app.py`:** Standardizes `apps/<app>/`, replaces default `tests.py` with structured test folders, and wires the app into `INSTALLED_APPS` and root version routing.
- **`_create_api.py`:** Generates API class, router wiring, URL entries, and related tests from templates, and blocks accidental overwrite of existing V1 API definitions.

### Model & Field Engineering

- **`_create_model.py`:** Enforces naming conventions (PascalCase + `Model`) and applies table naming (`tbl_<app>_<model_base>`).
- **`_create_modelfield.py`:** Adds foreign key fields only, using managed markers inside model classes instead of blind append operations, so generated code lands in a stable location.

## The Safety Model

This layer assumes file edits are high-impact, so safeguards are part of normal execution rather than optional extras.

- **File Guardians:** Top-level `run()` methods in managers/mutators are wrapped with `mo_helper_kit.file_guardian`.
- **Existence Checks:** Write targets are validated before mutation.
- **Controlled Deletion:** `nuke` safety behavior is detailed in [The Nuke Flow](#4-the-nuke-flow).

### How `file_guardian` Works Here

`file_guardian` is transaction-style rollback protection for file mutations inside a manager run:

1. It monkey-patches write surfaces (`open`, `os.makedirs`, `Path.write_text`, `Path.write_bytes`, `Path.mkdir`).
2. During execution it tracks newly created files/directories and modified files (with temporary backups in a rollback directory).
3. If execution succeeds, rollback backups are discarded.
4. If execution fails, created files are deleted, modified files are restored from backup, created directories are removed in reverse order, and patched functions are restored.

Practical boundary:

- It protects Python-side file operations executed inside the wrapped process.
- It does not roll back side effects produced inside external subprocesses (for example commands run by `subprocess.run`).

## Troubleshooting the Kit

Most issues come from registration gaps or manual edits inside generated areas.

1. **Registration:** Ensure new managers expose `register_subcommand`.
2. **Discovery behavior:** If a command does not appear in generated `mindoff.py`, verify discovery exclusions documented in [Command Discovery & Extension](#command-discovery-extension).
3. **Wiring:** If a route is unreachable, verify generated entries in `urls.py`.
4. **Markers:** If field generation fails, check whether the model insertion marker was removed.
5. **Direct command errors:** For direct usage, verify argument formats exactly (`<app>/<ModelName>`, `<app>/<api_name>`, `--to <app>/<ModelName>`).

