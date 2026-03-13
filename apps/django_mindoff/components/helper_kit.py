"""
Helper Kit
    pascal_to_snake
    get_current_app_name
    get_exact_traceback
    file_guardian
    get_api_class_from_url_name
    get_api_class_attributes
"""

import importlib
import inspect
import os
import re
import traceback
from pathlib import Path
from types import SimpleNamespace
from django.apps import apps
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from ._helper_kit import file_guardian


# ----------------
# Functions
# ----------------
def pascal_to_snake(name: str) -> str:
    """Convert PascalCase/CamelCase names to snake_case.

    Usage:

    ```python
    snake = mo_helper_kit.pascal_to_snake("CreateOrderAPI")
    # create_order_a_p_i
    ```

    Parameters:

    - `name` (`str`): Input class/style name.

    Possible responses:

    - Returns normalized snake_case string.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def get_app_module_path(app_name: str) -> str:
    """Resolve installed app import path from short app label.

    Usage:

    ```python
    app_module = get_app_module_path("orders")
    ```

    Parameters:

    - `app_name` (`str`): App label (last segment of installed app path).

    Possible responses:

    - Returns full import path from `INSTALLED_APPS`.
    - Raises `ValueError` when app cannot be resolved.
    """

    def _impl(app_name: str) -> str:
        for path in settings.INSTALLED_APPS:
            if path.rsplit(".", 1)[-1] == app_name:
                try:
                    importlib.import_module(path)
                    return path
                except ImportError:
                    continue
        raise ValueError(f"App '{app_name}' not found in INSTALLED_APPS.")

    return _impl(app_name)


def get_current_app_name():
    """Resolve current Django app label from caller file path.

    Usage:

    ```python
    app_name = mo_helper_kit.get_current_app_name()
    ```

    Possible responses:

    - Returns app label as `str`.
    - Raises `ValueError` when frame path cannot be mapped to a Django app.
    """
    frame = inspect.currentframe()
    try:
        frame = frame.f_back
        while frame:
            filename = frame.f_code.co_filename
            if filename and os.path.isabs(filename):
                app_config = apps.get_containing_app_config(filename)
                if app_config:
                    return app_config.label
            frame = frame.f_back
    finally:
        del frame
    raise ValueError("No app name could be resolved from current file location.")


def get_exact_traceback(*, skip: int | None = None) -> str:
    """Build filtered traceback text for project-owned frames.

    Usage:

    ```python
    tb_text = mo_helper_kit.get_exact_traceback(skip=0)
    ```

    Parameters:

    - `skip` (`int | None, default=None`):
      Frame index in filtered project stack. `None` returns all matching frames.

    Behavior:

    - Filters stack using `settings.MINDOFF_TRACEBACK_DIRS` (default: `apps`, `config`).

    Possible responses:

    - Returns formatted traceback string.
    - Returns fallback message when no project frame matches.
    """
    stack = inspect.stack()
    project_dirs = getattr(settings, "MINDOFF_TRACEBACK_DIRS", ["apps", "config"])
    project_dirs = [os.path.abspath(str(Path(d))) for d in project_dirs]
    valid_frames = []
    for frame_info in stack:
        filename = os.path.abspath(frame_info.filename)
        if any(filename.startswith(proj_dir + os.sep) for proj_dir in project_dirs):
            valid_frames.append(frame_info)
    if not valid_frames:
        return "No project frame found in traceback."
    if skip is None:
        summaries = [
            traceback.extract_stack(frame_info.frame, limit=1)[0]
            for frame_info in valid_frames
        ]
        return "".join(traceback.format_list(summaries))
    else:
        idx = min(skip, len(valid_frames) - 1)
        chosen_frame = valid_frames[idx]
        tb_summary = traceback.extract_stack(chosen_frame.frame, limit=1)
        return "".join(traceback.format_list(tb_summary))


def get_api_class_from_url_name(*, api_url_name: str, version: int = 1):
    """Resolve API class from a registered URL name and version.

    Usage:

    ```python
    api_cls = mo_helper_kit.get_api_class_from_url_name(
        api_url_name="orders__create_order",
        version=1,
    )
    ```

    Parameters:

    - `api_url_name` (`str`): Named URL route (e.g., `<app>__<api>`).
    - `version` (`int, default=1`): Router version key.

    Possible responses:

    - Returns API view class.
    - Raises `LookupError` when route name is not found.
    - Raises `KeyError`/`TypeError` for invalid version-router callback shape.
    """
    stack = list(get_resolver().url_patterns)
    while stack:
        pattern = stack.pop()
        if isinstance(pattern, URLResolver):
            stack.extend(pattern.url_patterns)
            continue
        if isinstance(pattern, URLPattern) and pattern.name == api_url_name:
            callback = _unwrap_callback(pattern.callback)
            return _get_api_class_from_callback(callback, api_url_name, version)
    raise LookupError(f"No URL found with name '{api_url_name}'")


def get_api_class_attributes(*, api_url_name: str, version: int = 1) -> dict:
    """Collect merged class attributes for API class resolved by URL name/version.

    Usage:

    ```python
    attrs = mo_helper_kit.get_api_class_attributes(
        api_url_name="orders__create_order",
        version=1,
    )
    ```

    Parameters:

    - `api_url_name` (`str`): Named URL route.
    - `version` (`int, default=1`): Version key for router-based APIs.

    Behavior:

    - Walks API class MRO and returns non-callable, non-private attributes.

    Possible responses:

    - Returns merged attribute dictionary.
    """
    api_cls = get_api_class_from_url_name(api_url_name=api_url_name, version=version)
    attrs = {}
    for cls in reversed(api_cls.__mro__):
        for name, value in vars(cls).items():
            if name.startswith("_"):
                continue
            if callable(value):
                continue
            attrs[name] = value
    return attrs


# ----------------
# Helper Functions
# ----------------
def _unwrap_callback(callback):
    while hasattr(callback, "__wrapped__"):
        callback = callback.__wrapped__
    return callback


def _get_api_class_from_callback(callback, api_url_name: str, version: int):
    view_class = getattr(callback, "view_class", None)
    if view_class:
        return view_class

    version_map = getattr(callback, "VERSION_MAP", None)
    if version_map is not None:
        if not version_map:
            raise TypeError(
                f"URL '{api_url_name}' resolves to a router with an "
                f"empty VERSION_MAP."
            )
        if version not in version_map:
            raise KeyError(
                f"Version {version} is not registered for '{api_url_name}'. "
                f"Available versions: {sorted(version_map.keys())}"
            )
        return version_map[version]

    raise TypeError(
        f"URL '{api_url_name}' is not a class-based view or a "
        f"version-router instance. The callback has neither a "
        f"'view_class' nor a 'VERSION_MAP' attribute."
    )


# ----------------
# Entry Point
# ----------------
mo_helper_kit = SimpleNamespace(
    pascal_to_snake=pascal_to_snake,
    get_current_app_name=get_current_app_name,
    get_exact_traceback=get_exact_traceback,
    file_guardian=file_guardian.file_guardian,
    get_api_class_from_url_name=get_api_class_from_url_name,
    get_api_class_attributes=get_api_class_attributes,
)
