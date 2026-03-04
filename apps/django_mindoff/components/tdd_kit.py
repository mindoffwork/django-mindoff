import shutil
import sys
import uuid
import tempfile
import os
import importlib
from pathlib import Path
from typing import List, Tuple, Type, get_args, get_origin, Literal

import polars as pl
import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection, models
from django.test import SimpleTestCase, override_settings
from django.urls import get_resolver, URLResolver, URLPattern
from django.core.exceptions import ImproperlyConfigured
from .helper_kit import mo_helper_kit
from .validation_kit import mo_validation_kit
from model_bakery import baker
from typeguard import typechecked

from ._tdd_kit import field_value_generator
from django.urls import reverse, resolve, Resolver404
from rest_framework.test import APIClient
from .managers._create_app import DjangoAppCreator
from django.urls import clear_url_caches
from django.contrib.auth import get_user_model

# Import the thread-local flag used to bypass queue mode during tests.
from .api_kit import _test_force_direct


# ----------------
# Constants
# ----------------
PASCAL_CASE_REGEX = r"^[A-Z][a-zA-Z0-9]+$"
SNAKE_CASE_REGEX = r"^[a-z0-9_]+$"
test_case = SimpleTestCase()
json_key = "application/json"


# ----------------
# Classes
# ----------------
class MindoffTestCase:
    @pytest.fixture(autouse=True)
    def run(self, request):
        self.asserts = request.getfixturevalue("_asserts")
        self.mo_mock_app = request.getfixturevalue("_mo_mock_app")
        self.mo_mock_model = request.getfixturevalue("_mo_mock_model")
        self.mo_mock_model_frms = request.getfixturevalue("_mo_mock_model_frms")
        self.mo_update_mock_model_frms = request.getfixturevalue(
            "_mo_update_mock_model_frms"
        )
        self.mo_call_api = request.getfixturevalue("_mo_call_api")
        self.mo_assert_api_response = request.getfixturevalue("_mo_assert_api_response")
        self.mo_create_user = request.getfixturevalue("_mo_create_user")
        self.client = APIClient()
        if hasattr(mo_validation_kit, "reset"):
            mo_validation_kit.reset()

    @pytest.fixture(scope="session")
    def _asserts(self):
        return SimpleTestCase()

    @pytest.fixture
    def _mo_mock_app(self, request):
        """
        Temporary Django App Creation Fixture (isolated, real-structure).
        """
        from typing import NamedTuple

        class CreatedApp(NamedTuple):
            app_name: str
            temp_dir: str
            override: bool
            creator: str

        created_apps: list[CreatedApp] = []

        def __setup(app_name: str | None = None, *, is_return_path: bool = False):
            app_name = _validate_or_generate_app_name(created_apps, app_name)
            app_name = app_name.lower().replace(" ", "_")

            # Create a unique temp directory for this specific app
            temp_dir = Path(tempfile.mkdtemp()).resolve()

            # Add the temp_dir to the START of sys.path
            # This ensures that when we say 'import {app_name}', Python looks here first
            sys.path.insert(0, str(temp_dir))

            # In isolation mode, the dotted_path is just the app_name itself
            dotted_path = app_name
            app_dir = temp_dir / app_name

            # --- Create app ---
            # We use dotted_path (just the name) so apps.py shows: name = 'test_app'
            creator = DjangoAppCreator(dotted_path, isolated=True)
            creator.project_root = temp_dir
            creator.app_dir = str(app_dir)
            creator.settings_path = temp_dir / "dummy_settings.py"
            creator.urls_path = temp_dir / "dummy_urls.py"
            creator.run()

            # --- URL and Settings Overrides ---
            mock_root_urlconf_name = f"urls_{app_name}"
            mock_root_path = temp_dir / f"{mock_root_urlconf_name}.py"

            mock_root_content = f"""
from django.urls import path, include
from {settings.ROOT_URLCONF} import urlpatterns as original_patterns
import {dotted_path}.urls

urlpatterns = original_patterns + [
    path('{app_name}/', include('{dotted_path}.urls')),
]
            """
            mock_root_path.write_text(mock_root_content)

            override = override_settings(
                INSTALLED_APPS=list(settings.INSTALLED_APPS) + [dotted_path],
                ROOT_URLCONF=mock_root_urlconf_name,
            )
            override.enable()

            apps.set_installed_apps(settings.INSTALLED_APPS)
            apps.clear_cache()
            clear_url_caches()
            if is_return_path:
                return (
                    app_name,
                    temp_dir,
                )
            return app_name

        def __teardown():
            while created_apps:
                app_name, temp_dir, override, _ = created_apps.pop()
                override.disable()
                root_url_mod = f"urls_{app_name}"

                # CRITICAL: Clear URL caches BEFORE removing modules
                clear_url_caches()

                # Remove app modules - be thorough with all submodules
                mods_to_remove = [
                    mod_name
                    for mod_name in (sys.modules.keys())
                    if (
                        mod_name == app_name
                        or mod_name.startswith(f"{app_name}.")
                        or mod_name == root_url_mod
                        or mod_name.startswith(f"{root_url_mod}.")
                    )
                ]

                for mod_name in mods_to_remove:
                    sys.modules.pop(mod_name, None)

                # Remove temp directory from sys.path
                sys.path[:] = [p for p in sys.path if str(p) != str(temp_dir)]

                # Clean up filesystem
                shutil.rmtree(temp_dir, ignore_errors=True)

            # Final Django house-cleaning - AFTER all apps are removed
            apps.clear_cache()
            clear_url_caches()
            apps.populate(settings.INSTALLED_APPS)

        request.addfinalizer(__teardown)
        return __setup

    @pytest.fixture
    def _mo_mock_model(self, request):
        created_models = []
        auto_created_app = None

        @typechecked
        def __setup(
            model_name: str | None = None,
            *,
            app_name: str | None = None,
            table_name: str | None = None,
            foreign_keys: List[Tuple[str, str] | Tuple[str, str, str]] = [],
            fields: dict = {},
            base_model=models.Model,
        ):
            status, foreign_keys = _normalize_fk_and_validate_mockmodel_params(
                model_name, table_name, foreign_keys
            )
            mo_validation_kit.ensure_truthy(status)
            nonlocal auto_created_app
            resolved_app = _resolve_app_name(auto_created_app, app_name)
            model_name = _validate_or_generate_model_name(created_models, model_name)
            table_name = table_name or mo_helper_kit.pascal_to_snake(
                model_name.lower().removesuffix("model")
            )
            model_class = _create_model(
                resolved_app, model_name, table_name, foreign_keys, fields, base_model
            )
            with connection.schema_editor() as editor:
                if model_class._meta.db_table in connection.introspection.table_names():
                    editor.delete_model(model_class)
                editor.create_model(model_class)
            created_models.append(model_class)
            _validate_model(model_class)
            return model_class

        def __teardown():
            if created_models:
                with connection.schema_editor() as editor:
                    for model in created_models:
                        editor.delete_model(model)
            if auto_created_app:
                _cleanup_dynamic_app(auto_created_app)

        request.addfinalizer(__teardown)
        return __setup

    @pytest.fixture
    def _mo_mock_model_frms(self, request):
        @typechecked
        def __bake_model_frm_dict(
            models: List[Type],
            *,
            counts: List[int] = [],
            exclude_columns: List[List[str]] = [],
            modify: List[dict] = [],
            is_fk_as_id: bool = True,
            is_enforce_db_column: bool = True,
            is_uuid_hex: bool = True,
        ) -> dict[Type, pl.DataFrame]:
            counts = counts or [1] * len(models)
            exclude_columns = exclude_columns or []
            modify = modify or []
            df_dict = {}
            baked_objects_per_model = []
            for idx, model in enumerate(models):
                objs = _generate_model_factory_objects(
                    idx, model, models, baked_objects_per_model, counts, is_uuid_hex
                )
                baked_objects_per_model.append(objs)
                df = pl.DataFrame(
                    [_obj_to_dict(obj, is_fk_as_id=is_fk_as_id) for obj in objs]
                )
                if is_enforce_db_column:
                    field_map = {
                        f.name: (f.db_column or f.get_attname_column()[1])
                        for f in model._meta.concrete_fields
                        if hasattr(f, "attname")
                    }
                    df = df.rename({col: field_map.get(col, col) for col in df.columns})
                df = _apply_exclude_columns(idx, df, exclude_columns)
                df = _apply_modify(idx, df, modify)
                df_dict[model] = df
            return df_dict

        return __bake_model_frm_dict

    @pytest.fixture
    def _mo_update_mock_model_frms(self, request):
        @typechecked
        def __update_model_frm_dict(
            df_dict: dict[Type, pl.DataFrame],
            *,
            counts: List[int] = [],
            exclude_columns: List[List[str]] = [],
            modify: List[dict] = [],
            keep_columns: List[List[str]] = [],
            is_uuid_hex: bool = True,
        ) -> dict[Type, pl.DataFrame]:
            updated_df_dict = {}
            models = list(df_dict.keys())
            counts = counts or [1] * len(models)
            baked_objects_per_model = []
            for idx, (model, df) in enumerate(df_dict.items()):
                new_df = df.clone()
                pk_name = model._meta.pk.db_column or model._meta.pk.attname
                fk_cols = [
                    (f.db_column or f.get_attname_column()[1])
                    for f in model._meta.concrete_fields
                    if f.is_relation
                ]
                user_keep = set(keep_columns[idx] if idx < len(keep_columns) else [])
                keep = {pk_name, *fk_cols, *user_keep}

                update_cols = [c for c in new_df.columns if c not in keep]
                new_df = _apply_exclude_columns(idx, new_df, exclude_columns)

                objs = _generate_model_factory_objects(
                    idx, model, models, baked_objects_per_model, counts, is_uuid_hex
                )
                baked_objects_per_model.append(objs)
                new_data = pl.DataFrame(
                    [_obj_to_dict(obj, is_fk_as_id=True) for obj in objs]
                )
                field_map = {
                    f.name: (f.db_column or f.get_attname_column()[1])
                    for f in model._meta.concrete_fields
                    if hasattr(f, "attname")
                }
                new_data = new_data.rename(
                    {col: field_map.get(col, col) for col in new_data.columns}
                )
                for col in update_cols:
                    if col in new_data.columns:
                        new_df = new_df.with_columns(new_data[col].alias(col))
                new_df = _apply_modify(idx, new_df, modify)
                updated_df_dict[model] = new_df
            return updated_df_dict

        return __update_model_frm_dict

    @pytest.fixture
    def _mo_call_api(self, request):
        @typechecked
        def __call(
            api_url_name: str,
            *,
            user=None,
            headers: dict | None = None,
            payload: list | dict | None = None,
            url_kwargs: dict | None = None,
            query_params: dict | None = None,
            **extra,
        ):
            from urllib.parse import urlencode

            resolved_url_kwargs = url_kwargs or {}
            is_versioned = _is_versioned_url(api_url_name)

            if is_versioned:
                if "version" not in resolved_url_kwargs:
                    raise ValueError(
                        f"[{api_url_name}] is a versioned API. "
                        f"'version' must be provided in url_kwargs, "
                        f"e.g. url_kwargs={{'version': 1}}"
                    )
                version = int(resolved_url_kwargs["version"])
            else:
                version = None

            api_cls_attr = _get_api_cls_attributes(api_url_name, version=version)
            JSON_CT = json_key

            method = getattr(api_cls_attr, "method", "get").lower()

            url = reverse(api_url_name, kwargs=resolved_url_kwargs)
            if query_params:
                url = f"{url}?{urlencode(query_params)}"
            if user is not None:
                self.client.force_authenticate(user=user)
            final_headers = {"Accept": JSON_CT}
            if headers:
                final_headers.update(headers)
            if (
                method in {"post", "put", "patch"}
                and "Content-Type" not in final_headers
            ):
                final_headers["Content-Type"] = JSON_CT
            mo_validation_kit.ensure_in(
                method, ["get", "post", "put", "patch", "delete"], is_exception=True
            )
            if method in {"get", "delete"}:
                mo_validation_kit.ensure_falsey(
                    payload,
                    msg=(
                        f"{method.upper()} requests cannot have a payload. "
                        f"Use query_params instead."
                    ),
                    is_exception=True,
                )

            # ── Dispatch (queue-mode APIs run synchronously via direct-mode override) ──
            _test_force_direct.active = True
            try:
                client_method = getattr(self.client, method)
                if method in {"get", "delete"}:
                    response = client_method(url, headers=final_headers, **extra)
                else:
                    response = client_method(
                        url,
                        data=payload or {},
                        format="json",
                        headers=final_headers,
                        **extra,
                    )
            finally:
                _test_force_direct.active = False

            return response

        return __call

    @pytest.fixture
    def _mo_assert_api_response(self, request):
        @typechecked
        def __assert(
            *,
            api_url_name: str,
            response,
            expected_response_type: Literal[
                "json", "plain", "html", "binary", "others"
            ] = "json",
            expected_status_code: int = 200,
        ):
            assert response is not None, f"[{api_url_name}] API returned no response"
            assert response.status_code == expected_status_code, (
                f"[{api_url_name}] Expected HTTP {expected_status_code}, "
                f"got {response.status_code}"
            )

            content_type = response.headers.get("Content-Type", "").lower()

            # ── JSON ──────────────────────────────────────────────────────────
            if expected_response_type == "json":
                assert (
                    json_key in content_type
                ), f"[{api_url_name}] Expected JSON, got Content-Type={content_type}"

            # ── Binary ───────────────────────────────────────────────────────
            elif expected_response_type == "binary":
                assert (
                    content_type
                ), f"[{api_url_name}] Binary response missing Content-Type"
                assert not any(
                    content_type.startswith(t)
                    for t in ("text/", json_key, "application/xml")
                ), f"[{api_url_name}] Binary response has unexpected text Content-Type: {content_type}"
                assert isinstance(
                    response.content, (bytes, bytearray)
                ), f"[{api_url_name}] Binary response must be bytes"

            # ── HTML ──────────────────────────────────────────────────────────
            elif expected_response_type == "html":
                assert (
                    "text/html" in content_type
                ), f"[{api_url_name}] Expected HTML, got Content-Type={content_type}"

            # ── Plain ─────────────────────────────────────────────────────────
            elif expected_response_type == "plain":
                assert (
                    "text/plain" in content_type
                ), f"[{api_url_name}] Expected plain text, got Content-Type={content_type}"

        return __assert

    @pytest.fixture
    def _mo_create_user(self, request):
        """
        Creates a Django user using model_bakery.
        If username is not provided, baker generates a random one.
        """

        def __create(username=None, password="password123", **extra_fields):
            user_model = get_user_model()

            if username:
                existing = user_model.objects.filter(username=username).first()
                if existing:
                    return existing
                extra_fields["username"] = username

            user = baker.make(user_model, **extra_fields)

            if password:
                user.set_password(password)
                user.save()

            return user

        return __create


class MindoffRouterTestCase:
    app_module: str
    router_function_name: str

    @pytest.fixture(autouse=True)
    def run(self, request):
        import importlib

        views = importlib.import_module(self.app_module)
        self.router = getattr(views, self.router_function_name)
        self.version_map = self.router.VERSION_MAP

    def test_every_version_dispatches_to_correct_class(self):
        """Each key in VERSION_MAP must dispatch to exactly the class it maps to."""
        from unittest.mock import patch, MagicMock
        from django.test import RequestFactory

        factory = RequestFactory()

        for version, expected_class in self.version_map.items():
            request = factory.get("/")
            with patch.object(
                expected_class,
                "as_view",
                return_value=lambda req, **kw: MagicMock(status_code=200),
            ) as mock_as_view:
                _ = self.router(request, version=version)
                assert mock_as_view.call_count == 1, (
                    f"Version {version} did not dispatch to {expected_class.__name__}. "
                    f"Expected as_view() to be called exactly once."
                )

    def test_unknown_version_returns_404_with_correct_body(self):
        """A version absent from VERSION_MAP must return HTTP 404 with detail and available_versions."""
        import json
        from django.test import RequestFactory
        from rest_framework.response import Response

        request = RequestFactory().get("/")
        response = self.router(request, version=99999)

        assert response.status_code == 404
        assert isinstance(response, Response)

        body = response.data
        assert body["message"]["code"] == "INVALID_API_VERSION"
        assert set(body["data"]["available_versions"]) == set(self.version_map.keys())


# ----------------
# Helper Functions
# ----------------
def _normalize_fk_and_validate_mockmodel_params(model_name, table_name, foreign_keys):
    if model_name:
        test_case.assertRegex(model_name, PASCAL_CASE_REGEX, msg="Invalid Model Name")
        test_case.assertTrue(
            model_name.endswith("Model"),
            msg=f"Model name '{model_name}' must end with 'Model'",
        )
    if table_name:
        test_case.assertRegex(table_name, SNAKE_CASE_REGEX)
    for idx, fk in enumerate(foreign_keys):
        fk_list = list(fk)
        model_name = fk_list[1]
        if model_name:
            test_case.assertRegex(model_name, PASCAL_CASE_REGEX)
        if len(fk_list) == 2:
            fk_list.append("required")
        elif len(fk_list) == 3:
            option = fk_list[2]
            mo_validation_kit.ensure_in(option, ["required", "optional"])
        foreign_keys[idx] = tuple(fk_list)
    return True, foreign_keys


def _ensure_dynamic_app():
    app_name = _validate_or_generate_app_name(created_apps=[])
    temp_dir = Path(tempfile.mkdtemp())
    app_path = temp_dir / app_name
    app_path.mkdir(parents=True, exist_ok=True)
    (app_path / "__init__.py").write_text("")
    (app_path / "models.py").write_text("from django.db import models\n")

    sys.path.insert(0, str(temp_dir))
    new_installed = list(settings.INSTALLED_APPS) + [app_name]
    override = override_settings(INSTALLED_APPS=new_installed)
    override.enable()
    apps.set_installed_apps(new_installed)

    return {
        "name": app_name,
        "temp_dir": temp_dir,
        "override": override,
    }


def _resolve_app_name(auto_created_app, provided_name: str | None) -> str:
    """Handles the logic for identifying which Django app to use."""
    if provided_name:
        return _validate_or_generate_app_name(app_name=provided_name, is_exists=True)

    try:
        return mo_helper_kit.get_current_app_name()
    except (ValueError, AttributeError, IndexError):
        if not auto_created_app:
            auto_created_app = _ensure_dynamic_app()
        return auto_created_app["name"]


def _cleanup_dynamic_app(app_data):
    name, temp_dir, override = (
        app_data["name"],
        app_data["temp_dir"],
        app_data["override"],
    )
    apps.app_configs.pop(name, None)
    sys.path[:] = [p for p in sys.path if p != str(temp_dir)]
    for mod in sys.modules.keys():
        if mod == name or mod.startswith(f"{name}."):
            sys.modules.pop(mod, None)
    shutil.rmtree(temp_dir, ignore_errors=True)
    override.disable()
    apps.clear_cache()
    apps.populate(settings.INSTALLED_APPS)


def _create_model(
    app_name: str,
    model_name: str,
    table_name: str,
    foreign_keys: list,
    fields: dict,
    base_model=models.Model,
):
    model_fields = {
        "id": models.UUIDField(
            primary_key=True,
            default=uuid.uuid4,
            editable=False,
            db_column="id",
        ),
        "__module__": __name__,
    }
    for main_app_name, main_model_name, option in foreign_keys:
        fk_name = f"{main_model_name.lower().removesuffix('model')}_ref"
        try:
            main_model_class = apps.get_model(main_app_name, main_model_name)
        except LookupError:
            raise LookupError(
                f"Foreign key target {main_app_name}.{main_model_name} not found in app registry"
            )
        if option == "optional":
            model_fields[fk_name] = models.ForeignKey(
                main_model_class,
                on_delete=models.CASCADE,
                db_column=f"{fk_name}_id",
                null=True,
                blank=True,
            )
        else:
            model_fields[fk_name] = models.ForeignKey(
                main_model_class, on_delete=models.CASCADE, db_column=f"{fk_name}_id"
            )
    model_fields.update(fields)

    class Meta:
        app_label = app_name
        db_table = f"tbl_{table_name}"

    model_fields["Meta"] = Meta

    def __str__(self):
        return str(getattr(self, self._meta.pk.attname))

    model_fields["__str__"] = __str__
    return type(model_name, (base_model,), model_fields)


def _validate_or_generate_app_name(
    created_apps: list = [], app_name: str | None = None, is_exists: bool = False
):
    existing_apps = set(apps.app_configs.keys()) | set(created_apps)
    if not app_name:
        app_name = f"app_{uuid.uuid4().hex[:12]}"
    if not is_exists and app_name in existing_apps:
        raise ValueError(f"App name '{app_name}' already exists.")
    elif is_exists and app_name not in existing_apps:
        raise ValueError(f"App name '{app_name}' does not exist.")
    return app_name


def _validate_or_generate_model_name(
    created_models: list = [], model_name: str | None = None
):
    existing_model_names = {m.__name__ for m in created_models}
    if not model_name:
        base_name = "TestModel"
        counter = 1
        while base_name in existing_model_names:
            base_name = f"Test{counter}Model"
            counter += 1
        model_name = base_name
    else:
        if model_name in existing_model_names:
            raise ValueError(f"Model name '{model_name}' already exists.")
    return model_name


def _validate_model(model_class):
    test_case.assertTrue(hasattr(model_class, "_meta"))
    test_case.assertTrue(model_class._meta.db_table)
    fields = {f.name: f for f in model_class._meta.concrete_fields}
    pk_name = model_class._meta.pk.name
    test_case.assertIn(pk_name, fields)
    for field in fields.values():
        if field.is_relation and field.many_to_one:
            test_case.assertIsNotNone(field.related_model)
            test_case.assertTrue(field.column)
    try:
        qs = model_class.objects.all()
        list(qs)
    except Exception as e:
        test_case.fail(f"Querying model failed: {e}")


def _obj_to_dict(obj, is_fk_as_id):
    result = {}
    for field in obj._meta.fields:
        val = getattr(obj, field.name)
        if is_fk_as_id and hasattr(field, "related_model") and val is not None:
            if hasattr(val, "pk"):
                val = val.pk
        result[field.name] = val
    return result


def _generate_model_factory_objects(
    idx, model, models, baked_objects_per_model, counts, is_uuid_hex
):
    n_per_parent = counts[idx]
    fk_fields_map = {
        f.name: baked_objects_per_model[i]
        for i, m in enumerate(models[:idx])
        for f in model._meta.fields
        if getattr(f, "related_model", None) is m
    }
    objs = []
    if not fk_fields_map:
        baked = _prepare_with_constraints(model, is_uuid_hex, quantity=n_per_parent)
        return baked if isinstance(baked, list) else [baked]
    immediate_parent_name, immediate_parent_objs = list(fk_fields_map.items())[-1]
    for parent_obj in immediate_parent_objs:
        fk_kwargs = {immediate_parent_name: parent_obj}
        for fk_name, fk_list in fk_fields_map.items():
            if fk_name == immediate_parent_name:
                continue
            if hasattr(parent_obj, fk_name):
                fk_kwargs[fk_name] = getattr(parent_obj, fk_name)
            else:
                fk_kwargs[fk_name] = fk_list[0]
        objs.extend(
            _prepare_with_constraints(
                model, is_uuid_hex, quantity=n_per_parent, **fk_kwargs
            )
        )
    return objs


def _prepare_with_constraints(model, is_uuid_hex, quantity=1, **fk_kwargs):
    objs = []
    used_uniques = {}
    for _ in range(quantity):
        kwargs = dict(fk_kwargs)
        for field in model._meta.fields:
            if field.name in kwargs:
                continue
            value = field_value_generator.generate_field_value(
                field, used_uniques, kwargs, is_uuid_hex
            )
            if value is not None:
                kwargs[field.name] = value
        objs.append(baker.prepare(model, **kwargs))
    return objs


def _apply_exclude_columns(idx, df, exclude_columns: List[List[str]]):
    cols = exclude_columns[idx] if idx < len(exclude_columns) else []
    if cols:
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"Cannot exclude non-existing columns {missing} "
                f"at index {idx}. Available columns: {list(df.columns)}"
            )
        return df.drop(cols)
    return df


def _apply_modify(idx, df, modify: List[dict]):
    changes = modify[idx] if idx < len(modify) else {}
    for row_idx, updates in changes.items():
        if not (0 <= row_idx < df.height):
            raise IndexError(
                f"Row index {row_idx} out of range for DataFrame with {df.height} rows "
                f"(modify idx={idx})."
            )
        for col, val in updates.items():
            if col not in df.columns:
                raise ValueError(
                    f"Cannot modify non-existing column '{col}' at index {idx}. "
                    f"Available columns: {list(df.columns)}"
                )
            try:
                df = df.with_columns(
                    [
                        pl.when(pl.arange(0, df.height) == row_idx)
                        .then(pl.lit(val, allow_object=True))
                        .otherwise(pl.col(col))
                        .alias(col)
                    ]
                )
            except Exception as e:
                raise ValueError(
                    f"Failed to modify value in column '{col}' at row {row_idx}. "
                    f"Attempted value: {val!r}. Original error: {e}"
                )
    return df


def _is_versioned_url(api_url_name: str) -> bool:
    stack = list(get_resolver().url_patterns)
    while stack:
        pattern = stack.pop()
        if isinstance(pattern, URLResolver):
            stack.extend(pattern.url_patterns)
            continue
        if isinstance(pattern, URLPattern) and pattern.name == api_url_name:
            callback = pattern.callback
            while hasattr(callback, "__wrapped__"):
                callback = callback.__wrapped__
            return hasattr(callback, "VERSION_MAP")
    return False


def _get_api_cls_attributes(api_url_name: str, version: int | None = None):
    stack = list(get_resolver().url_patterns)
    while stack:
        pattern = stack.pop()
        if isinstance(pattern, URLResolver):
            stack.extend(pattern.url_patterns)
            continue
        if isinstance(pattern, URLPattern) and pattern.name == api_url_name:
            return __resolve_api_cls_from_callback(
                callback=pattern.callback,
                api_url_name=api_url_name,
                version=version,
            )

    raise LookupError(f"No URL found with name '{api_url_name}'")


def __resolve_api_cls_from_callback(callback, api_url_name: str, version: int | None):
    while hasattr(callback, "__wrapped__"):
        callback = callback.__wrapped__

    view_class = getattr(callback, "view_class", None)
    if view_class:
        return view_class

    version_map = getattr(callback, "VERSION_MAP", None)
    if version_map is not None:
        if not version_map:
            raise ImproperlyConfigured(
                f"Router for '{api_url_name}' has an empty VERSION_MAP."
            )
        if version not in version_map:
            raise KeyError(
                f"Version {version} is not registered for '{api_url_name}'. "
                f"Available versions: {sorted(version_map.keys())}"
            )
        return version_map[version]

    raise ImproperlyConfigured(
        f"URL '{api_url_name}' is not a class-based view or a "
        f"version-router instance. The callback has neither a "
        f"'view_class' nor a 'VERSION_MAP' attribute."
    )
