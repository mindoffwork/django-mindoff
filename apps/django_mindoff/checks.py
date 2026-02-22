from importlib.util import find_spec
import importlib
import pkgutil
import sys
from inspect import isclass

from django.apps import apps as django_apps
from django.core.checks import Error, register

REQUIRED_INTEGRATION_DEPENDENCIES = [
    ("djangorestframework", "rest_framework"),
    ("python-decouple", "decouple"),
    ("django-ratelimit", "django_ratelimit"),
    ("typeguard", "typeguard"),
    ("polars", "polars"),
    ("pandas", "pandas"),
    ("sqlalchemy", "sqlalchemy"),
    ("orjson", "orjson"),
    ("pyarrow", "pyarrow"),
    ("dramatiq", "dramatiq"),
    ("redis", "redis"),
]


@register()
def check_mindoff_api_configs(app_configs, **kwargs):
    errors = []
    missing = _get_missing_dependencies()
    if missing:
        dep_list = ", ".join(missing)
        errors.append(
            Error(
                f"Missing django-mindoff integration dependencies: {dep_list}",
                hint=(
                    "Install required dependencies first. Example: "
                    f"pip install {' '.join(missing)}"
                ),
                id="django_mindoff.DEPENDENCY_ERR",
            )
        )
        return errors

    try:
        from .components.api_kit import MindoffAPIMixin
    except Exception as exc:
        errors.append(
            Error(
                f"Failed to import django-mindoff API components: {exc}",
                hint=(
                    "Ensure django-mindoff integration dependencies are installed "
                    "and importable in this environment."
                ),
                id="django_mindoff.DEPENDENCY_ERR",
            )
        )
        return errors

    for app_config in django_apps.get_app_configs():
        apis_dir = (
            app_config.path and __import__("pathlib").Path(app_config.path) / "apis"
        )
        if not apis_dir or not apis_dir.is_dir():
            continue

        # Walk every .py file under apis/ and import it fresh
        package_name = f"{app_config.name}.apis"
        for finder, module_name, _ in pkgutil.walk_packages(
            path=[str(apis_dir)],
            prefix=f"{package_name}.",
            onerror=lambda name: None,
        ):
            # Force a fresh import — evict any cached version first
            sys.modules.pop(module_name, None)
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue

            for attr_name in dir(module):
                obj = getattr(module, attr_name, None)
                if (
                    isclass(obj)
                    and issubclass(obj, MindoffAPIMixin)
                    and obj is not MindoffAPIMixin
                ):
                    _validate_view_class(obj, errors)

    return errors


def _validate_view_class(view_class, errors):
    try:
        instance = view_class()
        instance.validate_api_configuration()
    except Exception as exc:
        error_code = getattr(exc, "code", "API_CONFIG_ERR")
        errors.append(
            Error(
                f"API Configuration Error in {view_class.__name__}: {str(exc)}",
                hint="Check validate_api_configuration() in your API class.",
                obj=view_class,
                id=f"django_mindoff.{error_code}",
            )
        )


def _get_missing_dependencies():
    missing = []
    for pip_name, module_name in REQUIRED_INTEGRATION_DEPENDENCIES:
        if find_spec(module_name) is None:
            missing.append(pip_name)
    return missing
