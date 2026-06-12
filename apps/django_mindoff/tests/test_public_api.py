"""
Public API surface contract.

These names are the only stable, frozen import surface for end users:
`from django_mindoff import <name>`. The underlying `components` packages are
private and may be reorganized — this test guards against the surface drifting
away from what `__all__` advertises.

The full surface is exercised through the *installed* app namespace
(`apps.django_mindoff` in this dev repo), because this repository installs the
package under two names at once — `apps.django_mindoff` (INSTALLED_APPS) and
`django_mindoff` (editable install). Forcing the model-bearing kits to import
under the bare `django_mindoff` name would re-register `MOQueue`/`User` and
raise Django's app-label error. The lazy re-export mechanism itself is
namespace-agnostic, so validating it under the installed name proves it works
for the published `django_mindoff` name too.
"""

import importlib

import pytest

PACKAGE = "apps.django_mindoff"

EXPECTED_PUBLIC = {
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
}


@pytest.fixture(scope="module")
def pkg():
    return importlib.import_module(PACKAGE)


def test_all_matches_expected_surface(pkg):
    assert set(pkg.__all__) == EXPECTED_PUBLIC


def test_all_matches_export_map_keys(pkg):
    # __all__ and the lazy export map must never drift apart.
    assert set(pkg.__all__) == set(pkg._PUBLIC_EXPORTS)


def test_every_public_name_resolves_via_getattr(pkg):
    for name in pkg.__all__:
        assert getattr(pkg, name) is not None


def test_public_objects_are_the_component_singletons(pkg):
    validation_kit = importlib.import_module(f"{PACKAGE}.components.validation_kit")
    response_kit = importlib.import_module(f"{PACKAGE}.components.response_kit")
    api_kit = importlib.import_module(f"{PACKAGE}.components.api_kit")
    tdd_kit = importlib.import_module(f"{PACKAGE}.components.tdd_kit")

    assert pkg.MindoffValidationError is validation_kit.MindoffValidationError
    assert pkg.mo_validation_kit is validation_kit.mo_validation_kit
    assert pkg.mo_response_kit is response_kit.mo_response_kit
    assert pkg.MindoffAPIMixin is api_kit.MindoffAPIMixin
    assert pkg.mo_api_kit is api_kit.mo_api_kit
    assert pkg.MindoffTestCase is tdd_kit.MindoffTestCase
    assert pkg.MindoffRouterTestCase is tdd_kit.MindoffRouterTestCase


def test_unknown_attribute_raises_attribute_error(pkg):
    with pytest.raises(AttributeError):
        pkg.this_attribute_is_not_public


def test_dir_lists_public_surface(pkg):
    assert EXPECTED_PUBLIC.issubset(set(dir(pkg)))


def test_published_package_name_resolves_model_free_surface():
    # `django_mindoff` is importable as the published top-level name. Resolve a
    # model-free export through it to confirm the relative-import wiring works
    # under that name without pulling in the model-bearing kits.
    published = importlib.import_module("django_mindoff")
    assert published.MindoffValidationError is not None
    assert set(published.__all__) == EXPECTED_PUBLIC
