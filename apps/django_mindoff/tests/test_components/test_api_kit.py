import re
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.checks import run_checks
from django.urls import clear_url_caches, reverse
from rest_framework.test import APIClient

from ...components.tdd_kit import MindoffTestCase
from ...components.response_kit import mo_response_kit
from ...components.helper_kit import mo_helper_kit

User = get_user_model()
paddword = "pass123"

# ========================================================================================
# ⚓ CONSTANTS
# ========================================================================================
ALLOWED_METHODS = ["get", "post", "put", "delete"]
ALLOWED_PROCESS_MODES = ["direct", "queue"]
ALLOWED_RESPONSE_TYPES = ["json", "plain", "html", "xml", "binary", "others"]
VALIDATION_MODES = ["strict", "basic", None]

# ---------------------------------------------------------------------------
# Canonical name constants used to generate the one-time template.
# Must be purely alphabetic + underscores (no numbers) to pass name validation.
# ---------------------------------------------------------------------------
_CANONICAL_API = "canonical_api"
_CANONICAL_CLASS = "CanonicalApiV1APIView"
_CANONICAL_ROUTER_CLASS = "CanonicalApiRouter"
_CANONICAL_ROUTER_INST = "canonical_api_router"
_CANONICAL_HUMAN = "Canonical Api"
_CANONICAL_APP = "canonical_bootstrap_app"


# ========================================================================================
# 🔧 SESSION-SCOPED TEMPLATE FIXTURE
# ========================================================================================


@pytest.fixture(scope="session")
def _api_templates(tmp_path_factory):
    """
    Calls DjangoApiCreator.run() exactly ONCE per test session.

    Captures the three files it writes — apis/<n>.py, views.py, urls.py —
    as in-memory strings with the django_mindoff import fix already applied.
    The bootstrap app is torn down immediately after capture.

    All subsequent _create_test_api calls substitute canonical names and
    write 3 files directly — no DjangoApiCreator, no directory walking,
    no test-file generation.
    """
    import shutil
    from django.apps import apps as django_apps
    from django.conf import settings as django_settings
    from django.test import override_settings
    from django.urls import clear_url_caches as _clear

    from ...components.managers.create_api import DjangoApiCreator
    from ...components.managers.create_app import DjangoAppCreator

    tmp_root = Path(tmp_path_factory.mktemp("canonical_bootstrap"))
    sys.path.insert(0, str(tmp_root))

    # Stand up a minimal isolated app so DjangoApiCreator has somewhere to write
    app_creator = DjangoAppCreator(_CANONICAL_APP, isolated=True)
    app_creator.project_root = tmp_root
    app_creator.app_dir = str(tmp_root / _CANONICAL_APP)
    app_creator.settings_path = tmp_root / "dummy_settings.py"
    app_creator.urls_path = tmp_root / "dummy_urls.py"
    app_creator.run()

    ov = override_settings(
        INSTALLED_APPS=list(django_settings.INSTALLED_APPS) + [_CANONICAL_APP]
    )
    ov.enable()
    django_apps.set_installed_apps(django_settings.INSTALLED_APPS)

    # Run the creator exactly once
    DjangoApiCreator(
        api_path=f"{_CANONICAL_APP}/{_CANONICAL_API}",
        base_path=tmp_root,
    ).run()

    app_root = tmp_root / _CANONICAL_APP

    def _fix_imports(text: str) -> str:
        return re.sub(r"\bfrom django_mindoff\b", "from apps.django_mindoff", text)

    templates = {
        "api": _fix_imports((app_root / "apis" / f"{_CANONICAL_API}.py").read_text()),
        "views": _fix_imports((app_root / "views.py").read_text()),
        "urls": (app_root / "urls.py").read_text(),
    }

    # Tear down the bootstrap app completely before returning templates.
    # Order matters:
    # 1. Disable settings override first (restores INSTALLED_APPS to original)
    # 2. Evict ALL canonical modules from sys.modules before clear_cache
    #    so Django's app registry never tries to reimport them
    # 3. Remove tmp_root from sys.path so the module can't be found again
    # 4. clear_cache + populate to rebuild the registry cleanly
    ov.disable()
    _clear()

    # Evict every trace of the canonical app — including any submodule
    # Django may have imported during DjangoApiCreator.run()
    to_remove = [
        mod
        for mod in list(sys.modules.keys())
        if mod == _CANONICAL_APP or mod.startswith(f"{_CANONICAL_APP}.")
    ]
    for mod in to_remove:
        sys.modules.pop(mod, None)

    sys.path[:] = [p for p in sys.path if p != str(tmp_root)]
    shutil.rmtree(tmp_root, ignore_errors=True)

    # Restore app registry to original state — must happen after sys.modules cleanup
    django_apps.app_configs.pop(_CANONICAL_APP, None)
    django_apps.clear_cache()
    django_apps.populate(django_settings.INSTALLED_APPS)

    return templates


# ========================================================================================
# 🔧 HELPER FUNCTIONS
# ========================================================================================


def _pascal(snake: str) -> str:
    """canonical_api → CanonicalApi"""
    return "".join(w.capitalize() for w in snake.split("_"))


def _human(snake: str) -> str:
    """canonical_api → Canonical Api"""
    return " ".join(w.capitalize() for w in snake.split("_"))


def _reload_api_modules(app_name: str, api_name: str):
    """Force-evict both router and versioned API modules from sys.modules."""
    for mod in (f"{app_name}.views", f"{app_name}.apis.{api_name}"):
        sys.modules.pop(mod, None)


def _create_test_api(
    app_name: str,
    api_name: str,
    base_path: Path,
    templates: dict,
    url_patterns: list = None,
) -> str:
    """
    Fast-path API creation.

    Instead of calling DjangoApiCreator.run() (which writes 5 files and
    generates test scaffolding we never use), this substitutes canonical
    name tokens in the pre-captured template strings and writes exactly
    3 files: apis/<api_name>.py, views.py (appended), urls.py (appended).

    Returns the api_url_name, e.g. 'myapp__my_api'.
    """
    pascal = _pascal(api_name)
    human = _human(api_name)
    url_name = f"{app_name}__{api_name}"
    canonical_url_name = f"{_CANONICAL_APP}__{_CANONICAL_API}"

    def _sub(text: str) -> str:
        # Replacement order is critical — longest/most-specific first:
        # 1. canonical_url_name contains both _CANONICAL_APP and _CANONICAL_API
        # 2. _CANONICAL_ROUTER_INST contains _CANONICAL_API as a substring
        # 3. _CANONICAL_ROUTER_CLASS contains _CANONICAL_CLASS as a substring
        # 4. _CANONICAL_APP must precede _CANONICAL_API (no substring overlap,
        #    but explicit ordering prevents future regressions)
        return (
            text.replace(canonical_url_name, url_name)
            .replace(_CANONICAL_ROUTER_CLASS, f"{pascal}Router")
            .replace(_CANONICAL_ROUTER_INST, f"{api_name}_router")
            .replace(_CANONICAL_CLASS, f"{pascal}V1APIView")
            .replace(_CANONICAL_HUMAN, human)
            .replace(_CANONICAL_APP, app_name)
            .replace(_CANONICAL_API, api_name)
        )

    app_root = base_path / app_name

    # ── 1. apis/<api_name>.py ────────────────────────────────────────────────
    apis_dir = app_root / "apis"
    apis_dir.mkdir(parents=True, exist_ok=True)
    init = apis_dir / "__init__.py"
    if not init.exists():
        init.write_text("")
    (apis_dir / f"{api_name}.py").write_text(_sub(templates["api"]))

    # ── 2. views.py — append router block; merge imports deduped ────────────
    views_path = app_root / "views.py"
    new_views = _sub(templates["views"])

    if views_path.exists():
        existing = views_path.read_text()
        import_lines, code_lines = [], []
        for line in new_views.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                import_lines.append(s)
            else:
                code_lines.append(line)
        existing_set = set(existing.splitlines())
        missing = [l for l in import_lines if l not in existing_set]
        views_path.write_text(
            existing.rstrip()
            + ("\n" + "\n".join(missing) if missing else "")
            + "\n\n"
            + "\n".join(code_lines)
            + "\n"
        )
    else:
        views_path.write_text(new_views)

    # ── 3. urls.py — insert new path entry before closing ] ─────────────────
    urls_path = app_root / "urls.py"
    url_segment = url_patterns[0] if url_patterns else f"{api_name}/"
    new_entry = (
        f"    path('{url_segment}', "
        f"csrf_exempt(views.{api_name}_router), "
        f"name='{url_name}'),"
    )

    if urls_path.exists():
        urls_text = urls_path.read_text()
        # Ensure csrf_exempt is imported (only added once)
        if "csrf_exempt" not in urls_text:
            urls_text = (
                "from django.views.decorators.csrf import csrf_exempt\n" + urls_text
            )
        urls_text = re.sub(r"(\])", f"{new_entry}\n\\1", urls_text, count=1)
        urls_path.write_text(urls_text)
    else:
        # urls.py is always created by DjangoAppCreator — this is a safety fallback
        base = _sub(templates["urls"])
        if "csrf_exempt" not in base:
            base = "from django.views.decorators.csrf import csrf_exempt\n" + base
        urls_path.write_text(base)

    _reload_api_modules(app_name, api_name)
    clear_url_caches()
    return url_name


def _modify_api_attributes(
    app_name: str,
    api_name: str,
    attributes: dict,
    base_path: Path,
):
    """
    Modify one or more class-level attributes inside apis/<api_name>.py in a
    single read-modify-write pass (1 file read + 1 file write regardless of
    how many attributes are changed).
    """
    api_file = base_path / app_name / "apis" / f"{api_name}.py"
    if not api_file.exists():
        raise FileNotFoundError(f"API file not found at {api_file}")

    content = api_file.read_text()

    for attribute, value in attributes.items():
        if value is None:
            value_str = "None"
        elif isinstance(value, bool):
            value_str = str(value)
        elif isinstance(value, (list, dict)):
            value_str = str(value)
        elif isinstance(value, str):
            code_indicators = ["[", "{", "None"]
            if (
                any(value.strip().startswith(c) for c in code_indicators)
                or value.strip() == "None"
            ):
                value_str = value
            else:
                value_str = f'"{value}"'
        else:
            value_str = str(value)

        pattern = rf"(\s+{attribute}\s*:\s*[^=]+=\s*)(.+?)(\s*(?:#|$))"
        content = re.sub(
            pattern, rf"\g<1>{value_str}\g<3>", content, flags=re.MULTILINE
        )

    api_file.write_text(content)


def _modify_api_attribute(
    app_name: str,
    api_name: str,
    attribute: str,
    value,
    base_path: Path,
):
    """Single-attribute convenience wrapper around _modify_api_attributes."""
    _modify_api_attributes(app_name, api_name, {attribute: value}, base_path=base_path)


def _modify_api_run_method(
    app_name: str,
    api_name: str,
    run_code: str,
    base_path: Path,
):
    """Replace the body of run() inside apis/<api_name>.py."""
    api_file = base_path / app_name / "apis" / f"{api_name}.py"
    if not api_file.exists():
        raise FileNotFoundError(f"API file not found at {api_file}")

    content = api_file.read_text()
    pattern = (
        r"(def run\(self, request, \*args, \*\*kwargs\):)"
        r"([\s\S]*?)"
        r"(?=\n {4}def |\nclass |\Z)"
    )
    content = re.sub(pattern, rf"\1\n{run_code}\n", content, flags=re.DOTALL)
    api_file.write_text(content)


# ========================================================================================
# 🧪 TEST CLASSES
# ========================================================================================


@pytest.mark.django_db(transaction=True)
class TestAPIConfigurationValidation(MindoffTestCase):
    """
    Verifies API configuration validation via Django's checks framework.

    One shared mock app per class. Each test creates its own isolated API
    inside it. The _api_templates session fixture means DjangoApiCreator
    is never called from within this class.
    """

    EXPECTED_CHECK_ID = "django_mindoff.API_CONFIG_ERR"

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)

    def _assert_config_error(self, expected_msg_fragment: str):
        errors = run_checks()
        matching = [
            e
            for e in errors
            if e.id == self.EXPECTED_CHECK_ID and expected_msg_fragment in e.msg
        ]
        assert matching, (
            f"Expected config error with id={self.EXPECTED_CHECK_ID} "
            f"and message containing '{expected_msg_fragment}'.\n"
            f"Actual errors: {[(e.id, e.msg) for e in errors]}"
        )

    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("attribute", "value", "error_message"),
        [
            ("api_url_name", "", "`api_url_name` must not be empty"),
            ("api_name", "", "`api_name` must not be empty"),
        ],
    )
    def test_config_empty_required_attributes(self, attribute, value, error_message):
        """Verify empty string validation for required attributes"""
        api_name = f"test_empty_{attribute}_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(error_message)

    def test_config_invalid_api_url_name_not_in_urls(self):
        """Verify api_url_name must exist in urls.py"""
        api_name = "test_invalid_url_name_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "api_url_name",
            "non_existent_url_name",
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("not found in urls.py")

    def test_config_none_api_description(self):
        """Verify api_description cannot be None"""
        api_name = "test_none_desc_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_description", None, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(
            "`api_description` must be a string and cannot be None"
        )

    def test_config_non_boolean_allow_duplicate_queue(self):
        """Verify allow_duplicate_queue must be boolean"""
        api_name = "test_invalid_dup_queue_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "allow_duplicate_queue", "true", base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("`allow_duplicate_queue` must be boolean")

    @pytest.mark.parametrize(
        ("attribute", "value", "error_message"),
        [
            (
                "payload_schema",
                "invalid_string_schema",
                "`payload_schema` must be list | dict | None",
            ),
            (
                "max_payload_size",
                "not_a_number",
                "`max_payload_size` must be int | float | None",
            ),
        ],
    )
    def test_config_invalid_type_attributes(self, attribute, value, error_message):
        """Verify type validation for numeric/collection attributes"""
        api_name = f"test_invalid_{attribute}_type_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(error_message)

    def test_config_invalid_payload_validation_type(self):
        """Verify payload_validation accepts only 'strict', 'basic', or None"""
        api_name = "test_invalid_validation_type_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "payload_validation",
            "invalid_mode",
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(
            "`payload_validation` must be 'strict', 'basic' or None"
        )

    def test_config_invalid_rate_limit_format(self):
        """Verify api_request_limit matches '<int>/(s|m|h|d)' format"""
        api_name = "test_invalid_rate_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_request_limit", "abc/m", base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("must match '<int>/(s|m|h|d)' format")


@pytest.mark.django_db(transaction=True)
class TestAPIMixinAcceptance(MindoffTestCase):
    """
    Verifies valid API configurations work correctly at runtime.

    One shared mock app per class. Each test creates its own isolated API.
    Sequential attribute modifications are batched into a single file pass.
    """

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates
        self.user = User.objects.create_user(username="testuser", password=paddword)

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)

    # ------------------------------------------------------------------

    def test_api_success_with_generated_api(self):
        """ACCEPTANCE_1: API succeeds with as-is generated API"""
        api_url_name = self._make_api("test_basic_api")
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        "response_type", ["json", "plain", "html", "xml", "binary"]
    )
    def test_api_success_with_response_types(self, response_type):
        """ACCEPTANCE_2: API succeeds with valid response types"""
        api_name = f"test_response_type_{response_type}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "response_type", response_type, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize("http_method", ["get", "post", "put", "delete"])
    def test_api_success_with_allowed_methods(self, http_method):
        """ACCEPTANCE_3: API succeeds with allowed HTTP methods"""
        api_name = f"test_method_{http_method}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "method", http_method, base_path=self._dir
        )
        response = getattr(self.client, http_method)(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize("validation_mode", ["strict", "basic", None])
    def test_api_success_with_payload_validation_modes(self, validation_mode):
        """ACCEPTANCE_4: API succeeds with valid payload_validation modes"""
        safe_name = str(validation_mode).lower() if validation_mode else "none"
        api_name = f"test_validation_mode_{safe_name}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str, "age": int}',
                "payload_validation": validation_mode,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"name": "John", "age": 30}, format="json"
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_authenticated_user_with_auth_enabled(self):
        """ACCEPTANCE_5: API succeeds for authenticated users when auth enabled"""
        api_name = "test_auth_enabled_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "authentication_classes": "[BasicAuthentication]",
                "permission_classes": "[IsAuthenticated]",
            },
            base_path=self._dir,
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse(api_url_name))
        self.client.force_authenticate(user=None)
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_unauthenticated_user_with_auth_disabled(self):
        """ACCEPTANCE_6: API succeeds for unauthenticated users when auth disabled"""
        api_name = "test_public_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "permission_classes", "[AllowAny]", base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [
            ("api_request_limit", None),
            ("queue_status_streaming_limit", None),
            ("response_validation", False),
        ],
    )
    def test_api_success_optional_attribute_overrides(self, attribute, value):
        """ACCEPTANCE: API succeeds when optional attributes are overridden"""
        api_name = f"test_acceptance_{attribute}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_basic_validation_allows_extra_fields(self):
        """ACCEPTANCE: API succeeds when extra fields present in basic validation mode"""
        api_name = "test_basic_extra_fields_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"name": "John", "unexpected": "extra"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"


@pytest.mark.django_db(transaction=True)
class TestAPIMixinRejection(MindoffTestCase):
    """
    Verifies API properly rejects invalid requests and exceptions.

    One shared mock app per class. Each test creates its own isolated API.
    Parametrized tests that modify run() get uniquely-named APIs to prevent
    method body leakage across param cases.
    """

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates
        self.user = User.objects.create_user(username="testuser", password=paddword)

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)

    # ------------------------------------------------------------------

    @pytest.mark.parametrize("debug_mode", [False, True])
    def test_rejection_exception_from_run_method(self, settings, caplog, debug_mode):
        """REJECTION: Exception from run() is captured at DEBUG={debug_mode}"""
        settings.DEBUG = debug_mode
        # Unique per debug_mode to avoid run() body leakage
        api_name = f"test_exception_run_{str(debug_mode).lower()}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_run_method(
            self._app,
            api_name,
            '        raise ValueError("Test run exception")',
            base_path=self._dir,
        )
        with caplog.at_level(logging.ERROR):
            response = self.client.get(reverse(api_url_name))
        assert response.status_code == 500
        assert response.data["message"]["code"] == "UNEXPECTED_ERR"

    @pytest.mark.parametrize(
        ("configured_method", "requested_method"),
        [
            ("get", "post"),
            ("post", "get"),
            ("put", "delete"),
            ("delete", "post"),
        ],
    )
    def test_rejection_request_method_mismatch(
        self, configured_method, requested_method
    ):
        """REJECTION: Request method must match configured method"""
        api_name = f"test_mismatch_{configured_method}_{requested_method}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "method", configured_method, base_path=self._dir
        )
        response = getattr(self.client, requested_method)(reverse(api_url_name))
        assert (
            response.status_code == 400
            and response.data["message"]["code"] == "INVALID_METHOD"
        )

    def test_rejection_unauthenticated_user_with_auth_required(self):
        """REJECTION: Unauthenticated users rejected when auth enabled"""
        api_name = "test_auth_required_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "permission_classes": "[IsAuthenticated]",
                "authentication_classes": "[BasicAuthentication]",
            },
            base_path=self._dir,
        )
        response = self.client.get(reverse(api_url_name))
        assert response.data["message"]["code"] == "NOT_AUTHENTICATED"
        assert response.status_code == 401

    @patch("apps.django_mindoff.components.api_kit.is_ratelimited")
    def test_rejection_api_request_limit_exceeded(self, mock_limit):
        """REJECTION: API request exceeding rate limit is rejected"""
        cache.clear()
        mock_limit.return_value = True
        api_url_name = self._make_api("test_rate_limit_api")
        response = self.client.get(reverse(api_url_name))
        assert response.data["message"]["code"] == "API_RATE_LIMITED"

    def test_rejection_payload_size_exceeded(self):
        """REJECTION: Payload exceeding max_payload_size is rejected"""
        api_name = "test_size_exceeded_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 0.001,  # 1 KB
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 10000}, format="json"
        )
        assert response.data["message"]["code"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.parametrize(
        ("payload", "schema", "validation_mode", "error_code"),
        [
            (
                {"name": "John"},
                '{"name": str, "age": int}',
                "strict",
                "INVALID_PAYLOAD",
            ),
            (
                {"name": "John", "age": "thirty"},
                '{"name": str, "age": int}',
                "strict",
                "INVALID_PAYLOAD",
            ),
        ],
    )
    def test_rejection_payload_validation_strict_errors(
        self, payload, schema, validation_mode, error_code
    ):
        """REJECTION: Payload fails strict validation with missing/wrong-type fields"""
        has_wrong_type = "age" in payload and not isinstance(payload.get("age"), int)
        slug = "wrong_type" if has_wrong_type else "missing_field"
        api_name = f"test_strict_validation_{slug}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": schema,
                "payload_validation": validation_mode,
            },
            base_path=self._dir,
        )
        response = self.client.post(reverse(api_url_name), payload, format="json")
        assert response.data["message"]["code"] == error_code

    def test_rejection_payload_depth_exceeded(self):
        """REJECTION: Payload exceeding max_payload_depth is rejected"""
        api_name = "test_depth_exceeded_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"level1": {"level2": {"level3": str}}}',
                "payload_validation": "strict",
                "max_payload_depth": 2,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"level1": {"level2": {"level3": "value"}}},
            format="json",
        )
        assert response.data["message"]["code"] == "INVALID_PAYLOAD"

    def test_rejection_mindoff_validation_error_from_run(self):
        """REJECTION: MindoffValidationError raised in run() is handled"""
        api_name = "test_mindoff_validation_error_api"
        api_url_name = self._make_api(api_name)
        run_code = """
            from apps.django_mindoff.components.validation_kit import MindoffValidationError
            raise MindoffValidationError(
                code="VALIDATION_ERR",
                category="warning",
                data={"detail": "Custom validation failure"}
            )
        """
        _modify_api_run_method(self._app, api_name, run_code, base_path=self._dir)
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 400
        assert response.data["message"]["code"] == "VALIDATION_ERR"
        assert response.data["message"]["category"] == "warning"

    @pytest.mark.parametrize(
        (
            "exception_code",
            "exception_import",
            "exception_raise",
            "expected_status",
            "expected_code",
        ),
        [
            (
                "PERMISSION_DENIED",
                "from rest_framework.exceptions import PermissionDenied",
                'raise PermissionDenied("Access denied")',
                403,
                "PERMISSION_DENIED",
            ),
            (
                "RATE_LIMITED",
                "from rest_framework.exceptions import Throttled",
                "raise Throttled(wait=60)",
                429,
                "RATE_LIMITED",
            ),
        ],
    )
    def test_rejection_drf_exceptions_from_run(
        self,
        exception_code,
        exception_import,
        exception_raise,
        expected_status,
        expected_code,
    ):
        """REJECTION: DRF exceptions from run() are handled appropriately"""
        # Unique per exception type — run() is modified, must be isolated
        api_name = f"test_{exception_code.lower()}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_run_method(
            self._app,
            api_name,
            f"            {exception_import}\n            {exception_raise}",
            base_path=self._dir,
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == expected_status
        assert response.data["message"]["code"] == expected_code

    def test_rejection_payload_not_allowed_when_schema_none(self):
        """REJECTION: Non-empty payload rejected when payload_schema=None"""
        api_name = "test_payload_not_allowed_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": None,
                "payload_validation": "strict",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"unexpected": "data"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["message"]["code"] == "PAYLOAD_NOT_ALLOWED"


@pytest.mark.django_db(transaction=True)
class TestAPIMixinBoundary(MindoffTestCase):
    """
    Verifies API behavior at boundary and edge case conditions.

    One shared mock app per class. Each test creates its own isolated API.
    Sequential attribute modifications are batched into a single file pass.
    """

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)

    # ------------------------------------------------------------------

    @patch("apps.django_mindoff.components.api_kit.is_ratelimited")
    def test_boundary_api_request_at_exact_limit(self, mock_limit):
        """BOUNDARY: API succeeds when at exact request limit"""
        mock_limit.return_value = False
        api_url_name = self._make_api("test_limit_boundary_api")
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_size_at_exact_limit(self):
        """BOUNDARY: API succeeds when payload size equals max_payload_size"""
        api_name = "test_size_boundary_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 1,  # 1 MB
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 1048565}, format="json"
        )
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_validation_none_with_mismatched_payload(self):
        """BOUNDARY: API succeeds with mismatched payload when validation=None"""
        api_name = "test_validation_none_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str, "age": int}',
                "payload_validation": None,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"name": "John", "age": "not_an_int"},
            format="json",
        )
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_depth_exact(self):
        """BOUNDARY: API succeeds at exact max_payload_depth limit"""
        api_name = "test_depth_exact_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"level1": {"level2": {"level3": str}}}',
                "payload_validation": "strict",
                "max_payload_depth": 3,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"level1": {"level2": {"level3": "value"}}},
            format="json",
        )
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        ("rate_value", "description"),
        [
            ("1/s", "minimal_valid_rate"),
            ("9999/d", "large_but_valid_rate"),
        ],
    )
    def test_boundary_valid_rate_limits(self, rate_value, description):
        """BOUNDARY: API succeeds with valid extreme rate limits"""
        api_name = f"test_boundary_rate_{description}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_request_limit", rate_value, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_float_max_payload_size(self):
        """BOUNDARY: API succeeds when max_payload_size is a float"""
        api_name = "test_boundary_float_size_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 0.5,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 1000}, format="json"
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_missing_content_length_header(self):
        """BOUNDARY: API succeeds when CONTENT_LENGTH header is absent"""
        api_name = "test_boundary_missing_content_length_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 1,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"data": "small"},
            format="json",
            CONTENT_LENGTH="",
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"
