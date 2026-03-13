# Helper Kit

The Helper Kit is the shared introspection and utility layer of `django-mindoff`. It provides lightweight helpers for naming, app-resolution, traceback filtering, URL-to-API introspection, and file-mutation rollback protection.

Its role is infrastructural: multiple kits depend on it for consistent runtime behavior instead of re-implementing utility logic.

For API-builder-facing helper usage, see [Developer Guide - Helper Utilities](../developer_guide/helper-utilities.md).

<div class="admonition warning">
<p class="admonition-title">Stability Warning</p>
<p><code>mo_helper_kit</code> is not part of the core offering and is currently experimental and subject to change without any prior notice. Refer to this page to keep informed on development with helper utilities.</p>
</div>

## Architecture & Intent

Helper Kit has two parts:

1. `helper_kit.py` public helpers exposed through `mo_helper_kit` namespace.
2. `_helper_kit/file_guardian.py` transactional rollback decorator for file operations.

This separation keeps simple utility functions independent from mutation safety mechanics.

## Core Runtime Components

| Component | Responsibility | Examples |
| --- | --- | --- |
| **`mo_helper_kit` namespace** | Public callable surface used across kits/managers. | `pascal_to_snake`, `get_exact_traceback` |
| **URL/API introspection helpers** | Resolve API classes/attributes from URL names and version maps. | `get_api_class_from_url_name` |
| **Traceback filter helper** | Extract project-owned traceback lines only. | `get_exact_traceback` |
| **`file_guardian` decorator** | File mutation rollback guard for manager workflows. | `@mo_helper_kit.file_guardian` |

## Public Helper Surface

### Naming & App Resolution

- `pascal_to_snake(name)`: converts class-style names to snake_case.
- `get_current_app_name()`: walks call stack and resolves Django app label from file path.

### Traceback Filtering

- `get_exact_traceback(skip=None)`:
- Filters stack frames using `settings.MINDOFF_TRACEBACK_DIRS` (default `apps`, `config`).
- Returns either selected frame (`skip`) or formatted list of project-matched frames.

This keeps errors focused on project-owned code paths.

### URL-to-API Introspection

- `get_api_class_from_url_name(api_url_name, version=1)`:
- Traverses URL resolver tree.
- Supports both class-based callbacks (`view_class`) and version routers (`VERSION_MAP`).
- Raises clear errors for unknown routes, missing versions, or unsupported callback shapes.
- `get_api_class_attributes(...)`:
- Resolves API class then merges non-private, non-callable class attributes across MRO.

These are used for dynamic test/runtime inspection of API metadata.

### Namespace Boundary

`mo_helper_kit` exposes selected functions by design. Internal helper functions (for callback unwrapping and resolution plumbing) remain private.

## File Guardian Architecture

`file_guardian` is a decorator that wraps file-mutating manager flows with rollback semantics.

### What It Tracks

- Newly created files
- Newly created directories
- Modified existing files (with backups stored under a rollback temp directory)

### Instrumentation Model

During wrapped execution it monkey-patches:

- `builtins.open`
- `os.makedirs`
- `Path.write_text`
- `Path.write_bytes`
- `Path.mkdir`

### Success Path

1. Wrapped function completes.
2. Backup directory is deleted.
3. Original functions are restored.

### Failure Path

1. Created files are deleted.
2. Modified files are restored from backups.
3. Created directories are removed in reverse depth order.
4. Original functions are restored.
5. Original exception is re-raised.

This gives transaction-like safety for Python-side filesystem mutations.

## Integration With Other Runtime Layers

- **Management Kit:** mutator/manager `run()` methods use `@mo_helper_kit.file_guardian`.
- **Validation Kit / Response Kit:** use `get_exact_traceback` for focused diagnostics.
- **TDD Kit:** uses naming/app-resolution helpers in dynamic app/model setup.

Helper Kit acts as common plumbing beneath multiple architecture subsystems.

## Operational Caveats

- `file_guardian` protects Python-level file operations inside the current process only; external subprocess side effects are outside rollback scope.
- Monkey-patching is process-global while the wrapped function is running; avoid concurrent conflicting use patterns.
- `get_current_app_name()` depends on stack/file context and can fail in non-standard call environments.
- URL introspection helpers require stable route registration and expected callback/router shapes.

## Troubleshooting the Kit

1. **App name resolution fails:** verify helper is called from a file mapped to a Django app.
2. **API class lookup errors:** confirm URL name exists and version is registered in router `VERSION_MAP`.
3. **Missing attributes from introspection:** only non-private, non-callable class attributes are returned.
4. **Rollback did not revert external command writes:** `file_guardian` does not roll back subprocess-managed side effects.
5. **Unexpected rollback gaps:** ensure file mutations happen through tracked Python write surfaces inside the decorated execution path.
