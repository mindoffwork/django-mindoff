import importlib
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("django-mindoff")
except PackageNotFoundError:
    __version__ = "unknown"

# ----------------
# Public API surface
# ----------------
# These are the only names guaranteed stable for end users. The underlying
# `components` packages are private implementation detail and may be
# reorganized without notice — always import from `django_mindoff` directly.
#
# Re-exports are lazy (PEP 562) on purpose: importing `django_mindoff` must
# stay possible with a django-only install so the `init`/`nuke` CLI keeps
# working, while the kits below pull in the optional `internal` dependencies
# (DRF, polars, redis, …) only when actually accessed.
__all__ = [
    "mo_api_kit",
    "mo_crud_kit",
    "mo_helper_kit",
    "mo_polars_kit",
    "mo_response_kit",
    "mo_validation_kit",
    "MindoffAPIMixin",
    "MindoffValidationError",
    "MindoffTestCase",
    "MindoffRouterTestCase",
]

# name -> (relative module path, attribute)
_PUBLIC_EXPORTS = {
    "mo_api_kit": (".components.api_kit", "mo_api_kit"),
    "MindoffAPIMixin": (".components.api_kit", "MindoffAPIMixin"),
    "mo_crud_kit": (".components.crud_kit", "mo_crud_kit"),
    "mo_helper_kit": (".components.helper_kit", "mo_helper_kit"),
    "mo_polars_kit": (".components.polars_kit", "mo_polars_kit"),
    "mo_response_kit": (".components.response_kit", "mo_response_kit"),
    "mo_validation_kit": (".components.validation_kit", "mo_validation_kit"),
    "MindoffValidationError": (".components.validation_kit", "MindoffValidationError"),
    "MindoffTestCase": (".components.tdd_kit", "MindoffTestCase"),
    "MindoffRouterTestCase": (".components.tdd_kit", "MindoffRouterTestCase"),
}


def __getattr__(name):
    target = _PUBLIC_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    module = importlib.import_module(module_path, __name__)
    return getattr(module, attr)


def __dir__():
    return sorted([*globals(), *__all__])
