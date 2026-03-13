import argparse
import textwrap
from pathlib import Path
from unittest.mock import patch
import pytest
from ....components.managers._create_app import DjangoAppCreator
from ....components.managers._create_api import DjangoApiCreator
from ....components.managers._create_model import DjangoModelCreator
from ....components.managers._create_modelfield import DjangoModelFieldCreator
from ....components.managers._delete_app import DjangoAppDeleter
from ....components.managers import _create_app as create_app_module
from ....components.managers import _create_api as create_api_module
from ....components.managers import _create_model as create_model_module
from ....components.managers import _create_modelfield as create_modelfield_module
from ....components.managers import _delete_app as delete_app_module


FIELD_INSERT_MARKER = (
    "# Add model fields above this line -- "
    "(MANAGED BY MINDOFF. DO NOT TOUCH THIS LINE)"
)


class TestDjangoAppCreator:

    def test_init_prepends_apps_prefix_when_missing(self, tmp_path, monkeypatch):
        """REJECTION: Validates init prepends apps prefix when missing."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("billing")
        assert creator.dotted_path == "apps.billing"
        assert creator.original_path == "apps.billing"

    def test_init_does_not_double_prepend_apps_prefix(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates init does not double prepend apps prefix."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("apps.billing")
        assert creator.dotted_path == "apps.billing"

    def test_init_lowercases_app_segment(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates init lowercases app segment."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("Billing")
        assert creator.dotted_path == "apps.billing"
        assert creator.app_name == "billing"

    def test_normalize_path_raises_on_too_many_segments(self, tmp_path, monkeypatch):
        """REJECTION: Validates normalize path raises on too many segments."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Invalid App Name"):
            DjangoAppCreator("apps.nested.billing")

    def test_normalize_path_raises_on_empty_segment(self, tmp_path, monkeypatch):
        """REJECTION: Validates normalize path raises on empty segment."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            DjangoAppCreator("apps.")

    def test_app_dir_uses_dotted_path(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates app dir uses dotted path."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("shop")
        assert Path(creator.app_dir) == (tmp_path / "apps" / "shop")

    def test_create_directories_creates_app_dir_and_init(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create directories creates app dir and init."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("shop")
        creator._create_directories()
        assert (tmp_path / "apps" / "shop").is_dir()
        assert (tmp_path / "apps" / "__init__.py").exists()

    def test_create_directories_idempotent(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create directories idempotent."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("shop")
        creator._create_directories()
        creator._create_directories()  # must not raise
        assert (tmp_path / "apps" / "shop").is_dir()

    def test_run_startapp_calls_subprocess_with_correct_args(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates run startapp calls subprocess with correct args."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("shop")
        calls = []
        monkeypatch.setattr(
            create_app_module.subprocess,
            "run",
            lambda argv, check: calls.append(argv),
        )
        creator._run_startapp()
        assert calls == [["python", "manage.py", "startapp", "shop", creator.app_dir]]

    def test_overwrite_apps_py_writes_correct_content(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates overwrite apps py writes correct content."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("billing")
        (tmp_path / "apps" / "billing").mkdir(parents=True)
        creator._overwrite_apps_py()
        content = (tmp_path / "apps" / "billing" / "apps.py").read_text()
        assert "class BillingConfig(AppConfig):" in content
        assert "name = 'apps.billing'" in content
        assert "default_auto_field = 'django.db.models.BigAutoField'" in content

    def test_create_urls_py_writes_correct_structure(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create urls py writes correct structure."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("billing")
        (tmp_path / "apps" / "billing").mkdir(parents=True)
        creator._create_urls_py()
        content = (tmp_path / "apps" / "billing" / "urls.py").read_text()
        assert "from django.urls import path" in content
        assert "urlpatterns = [" in content
        assert "csrf_exempt" in content

    def test_create_serializers_py_writes_correct_content(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create serializers py writes correct content."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("billing")
        (tmp_path / "apps" / "billing").mkdir(parents=True)
        creator._create_serializers_py()
        content = (tmp_path / "apps" / "billing" / "serializers.py").read_text()
        assert "from rest_framework import serializers" in content
        assert "from . import models" in content

    def test_patch_models_py_inserts_mindoff_import(self, tmp_path, monkeypatch):
        """BOUNDARY: Validates patch models py inserts mindoff import."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            "from django.db import models\n\n# some code\n"
        )
        creator = DjangoAppCreator("shop")
        creator._patch_models_py()
        content = (app_dir / "models.py").read_text()
        assert "import uuid" in content
        assert "from django_mindoff import models as mindoff_models" in content

    def test_patch_models_py_skips_if_no_models_file(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates patch models py skips if no models file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "apps" / "shop").mkdir(parents=True)
        creator = DjangoAppCreator("shop")
        creator._patch_models_py()

    def test_patch_models_py_idempotent(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates patch models py idempotent."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        line = "import uuid\nfrom django_mindoff import models as mindoff_models\n"
        (app_dir / "models.py").write_text(line)
        creator = DjangoAppCreator("shop")
        creator._patch_models_py()
        assert (app_dir / "models.py").read_text().count("import uuid") == 1

    def test_patch_models_py_appends_when_all_lines_are_imports(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates patch models py appends when all lines are imports."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("from django.db import models\n")
        DjangoAppCreator("shop")._patch_models_py()
        assert "import uuid" in (app_dir / "models.py").read_text()

    def test_setup_tests_folder_creates_expected_directories(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates setup tests folder creates expected directories."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        DjangoAppCreator("shop")._setup_tests_folder()
        assert (app_dir / "tests" / "__init__.py").exists()
        assert (app_dir / "components" / "__init__.py").exists()
        assert (app_dir / "apis" / "__init__.py").exists()

    def test_setup_tests_folder_removes_tests_py_and_admin_py(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates setup tests folder removes tests py and admin py."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "tests.py").write_text("")
        (app_dir / "admin.py").write_text("")
        DjangoAppCreator("shop")._setup_tests_folder()
        assert not (app_dir / "tests.py").exists()
        assert not (app_dir / "admin.py").exists()

    def test_setup_tests_folder_does_not_fail_without_tests_py(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates setup tests folder does not fail without tests py."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        DjangoAppCreator("shop")._setup_tests_folder()

    def test_update_settings_adds_app_to_installed_apps(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates update settings adds app to installed apps."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        settings = config / "settings.py"
        settings.write_text("INSTALLED_APPS = [\n    'django.contrib.auth',\n]\n")
        DjangoAppCreator("shop")._update_settings()
        assert "'apps.shop'" in settings.read_text()

    def test_update_settings_skips_if_already_present(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates update settings skips if already present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        settings = config / "settings.py"
        settings.write_text(
            "INSTALLED_APPS = [\n    'django.contrib.auth',\n    'apps.shop',\n]\n"
        )
        DjangoAppCreator("shop")._update_settings()
        assert settings.read_text().count("'apps.shop'") == 1

    def test_update_settings_skips_when_no_installed_apps_marker(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates update settings skips when no installed apps marker."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        settings = config / "settings.py"
        settings.write_text("DEBUG = True\n")
        DjangoAppCreator("shop")._update_settings()
        assert "apps.shop" not in settings.read_text()

    def test_update_project_urls_inserts_route(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates update project urls inserts route."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        (config / "urls.py").write_text("urlpatterns = [\n]\n")
        DjangoAppCreator("shop")._update_project_urls()
        content = (config / "urls.py").read_text()
        assert "v<int:version>/shop/" in content
        assert "apps.shop.urls" in content

    def test_update_project_urls_skips_if_route_already_present(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates update project urls skips if route already present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        creator = DjangoAppCreator("shop")
        route = f"path('v<int:version>/shop/', include('{creator.dotted_path}.urls')),"
        (config / "urls.py").write_text(f"urlpatterns = [\n    {route}\n]\n")
        creator._update_project_urls()
        assert (config / "urls.py").read_text().count("apps.shop.urls") == 1

    def test_run_skips_if_app_dir_exists(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates run skips if app dir exists."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        creator = DjangoAppCreator("shop")
        called = []
        monkeypatch.setattr(creator, "_create_directories", lambda: called.append(1))
        creator.run()
        assert called == []

    def test_run_calls_all_steps_in_order(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates run calls all steps in order."""
        monkeypatch.chdir(tmp_path)
        creator = DjangoAppCreator("shop")
        order = []
        monkeypatch.setattr(
            creator, "_create_directories", lambda: order.append("dirs")
        )
        monkeypatch.setattr(creator, "_run_startapp", lambda: order.append("startapp"))
        monkeypatch.setattr(
            creator, "_overwrite_apps_py", lambda: order.append("apps_py")
        )
        monkeypatch.setattr(creator, "_create_urls_py", lambda: order.append("urls_py"))
        monkeypatch.setattr(
            creator, "_create_serializers_py", lambda: order.append("ser")
        )
        monkeypatch.setattr(creator, "_patch_models_py", lambda: order.append("patch"))
        monkeypatch.setattr(
            creator, "_setup_tests_folder", lambda: order.append("tests")
        )
        monkeypatch.setattr(
            creator, "_update_settings", lambda: order.append("settings")
        )
        monkeypatch.setattr(
            creator, "_update_project_urls", lambda: order.append("proj_urls")
        )
        creator.run()
        assert order == [
            "dirs",
            "startapp",
            "apps_py",
            "urls_py",
            "ser",
            "patch",
            "tests",
            "settings",
            "proj_urls",
        ]

    def test_register_subcommand_calls_creator_for_each_app(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates register subcommand calls creator for each app."""
        monkeypatch.chdir(tmp_path)
        args = _build_parser(create_app_module, "createapp")
        args.app_names = ["shop", "billing"]
        created = []
        with patch.object(
            DjangoAppCreator, "run", lambda self: created.append(self.app_name)
        ):
            args.handler(args)
        assert created == ["shop", "billing"]


class TestDjangoModelCreator:

    def test_normalize_app_name_prepends_apps(self):
        """ACCEPTANCE: Validates normalize app name prepends apps."""
        assert (
            DjangoModelCreator("shop/Order")._normalize_app_name("shop") == "apps.shop"
        )

    def test_normalize_app_name_already_prefixed(self):
        """ACCEPTANCE: Validates normalize app name already prefixed."""
        assert (
            DjangoModelCreator("shop/Order")._normalize_app_name("apps.shop")
            == "apps.shop"
        )

    def test_normalize_app_name_lowercases(self):
        """ACCEPTANCE: Validates normalize app name lowercases."""
        assert (
            DjangoModelCreator("shop/Order")._normalize_app_name("SHOP") == "apps.shop"
        )

    def test_normalize_app_name_raises_on_too_many_segments(self):
        """REJECTION: Validates normalize app name raises on too many segments."""
        with pytest.raises(ValueError, match="Invalid App Name"):
            DjangoModelCreator("shop/Order")._normalize_app_name("apps.nested.shop")

    def test_normalize_app_name_raises_on_empty_segment(self):
        """REJECTION: Validates normalize app name raises on empty segment."""
        with pytest.raises(ValueError):
            DjangoModelCreator("shop/Order")._normalize_app_name("apps.")

    def test_parse_input_sets_all_attributes(self):
        """ACCEPTANCE: Validates parse input sets all attributes."""
        c = DjangoModelCreator("shop/Invoice")
        assert c._parse_input() is True
        assert c.original_app_name == "shop"
        assert c.raw_model == "Invoice"
        assert c.app == "apps.shop"
        assert c.app_slug == "shop"

    def test_parse_input_returns_false_on_missing_slash(self):
        """REJECTION: Validates parse input returns false on missing slash."""
        assert DjangoModelCreator("shopOrder")._parse_input() is False

    def test_parse_input_returns_false_on_too_many_slashes(self):
        """ACCEPTANCE: Validates parse input returns false on too many slashes."""
        assert DjangoModelCreator("shop/Order/Extra")._parse_input() is False

    @pytest.mark.parametrize(
        "input_name, expected_final, expect_changes",
        [
            ("Invoice", "InvoiceModel", True),
            ("InvoiceModel", "InvoiceModel", False),
            ("invoice", "InvoiceModel", True),
            ("order_item", "OrderItemModel", True),
        ],
    )
    def test_format_model_name(self, input_name, expected_final, expect_changes):
        """ACCEPTANCE: Validates format model name."""
        c = DjangoModelCreator("shop/X")
        final, changes = c._format_model_name(input_name)
        assert final == expected_final
        assert bool(changes) == expect_changes

    def test_get_base_model_sets_parent_class(self):
        """ACCEPTANCE: Validates get base model sets parent class."""
        c = DjangoModelCreator("shop/Order")
        c._get_base_model()
        assert c.parent_class == "mindoff_models.TimeStampModel"

    def test_get_base_import_models(self):
        """ACCEPTANCE: Validates get base import models."""
        assert (
            DjangoModelCreator("x/Y")._get_base_import("models")
            == "from django.db import models"
        )

    def test_get_base_import_serializers(self):
        """ACCEPTANCE: Validates get base import serializers."""
        assert (
            DjangoModelCreator("x/Y")._get_base_import("serializers")
            == "from rest_framework import serializers"
        )

    def test_append_to_file_raises_on_invalid_kind(self, tmp_path):
        """REJECTION: Validates append to file raises on invalid kind."""
        with pytest.raises(ValueError, match="Invalid kind"):
            DjangoModelCreator("shop/Order")._append_to_file(
                tmp_path / "x.py", "content", "MyClass", kind="invalid"
            )

    def test_append_to_file_creates_new_file_with_base_import(self, tmp_path):
        """ACCEPTANCE: Validates append to file creates new file with base import."""
        c = DjangoModelCreator("shop/Order")
        path = tmp_path / "models.py"
        c._append_to_file(
            path, "class FooModel(Base):\n    pass", "FooModel", kind="models"
        )
        content = path.read_text()
        assert "from django.db import models" in content
        assert "class FooModel(Base):" in content

    def test_append_to_file_appends_to_existing_file(self, tmp_path):
        """ACCEPTANCE: Validates append to file appends to existing file."""
        c = DjangoModelCreator("shop/Order")
        path = tmp_path / "models.py"
        path.write_text(
            "from django.db import models\n\nclass BarModel(Base):\n    pass\n"
        )
        c._append_to_file(
            path, "class FooModel(Base):\n    pass", "FooModel", kind="models"
        )
        content = path.read_text()
        assert "class FooModel(Base):" in content
        assert "class BarModel(Base):" in content

    def test_append_to_file_skips_if_class_already_exists(self, tmp_path):
        """ACCEPTANCE: Validates append to file skips if class already exists."""
        c = DjangoModelCreator("shop/Order")
        path = tmp_path / "models.py"
        path.write_text("class FooModel(Base):\n    pass\n")
        original_mtime = path.stat().st_mtime
        c._append_to_file(
            path, "class FooModel(Base):\n    pass", "FooModel", kind="models"
        )
        assert path.stat().st_mtime == original_mtime

    def test_append_to_file_creates_parent_dirs_if_missing(self, tmp_path):
        """REJECTION: Validates append to file creates parent dirs if missing."""
        c = DjangoModelCreator("shop/Order")
        path = tmp_path / "deep" / "nested" / "models.py"
        c._append_to_file(
            path, "class FooModel(Base):\n    pass", "FooModel", kind="models"
        )
        assert path.exists()

    def test_generate_files_writes_model_and_serializer(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates generate files writes model and serializer."""
        monkeypatch.chdir(tmp_path)
        c = DjangoModelCreator("shop/Invoice")
        c._parse_input()
        c._get_base_model()
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("from django.db import models\n")
        (app_dir / "serializers.py").write_text(
            "from rest_framework import serializers\nfrom . import models\n"
        )
        c._generate_files()
        assert "class InvoiceModel(" in (app_dir / "models.py").read_text()
        assert (
            "class InvoiceModelSerializer(" in (app_dir / "serializers.py").read_text()
        )

    def test_generate_files_model_has_uuid_field_and_marker(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates generate files model has uuid field and marker."""
        monkeypatch.chdir(tmp_path)
        c = DjangoModelCreator("shop/Order")
        c._parse_input()
        c._get_base_model()
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("")
        (app_dir / "serializers.py").write_text("")
        c._generate_files()
        content = (app_dir / "models.py").read_text()
        assert "UUIDField" in content
        assert FIELD_INSERT_MARKER in content

    def test_generate_files_db_table_uses_app_slug_and_base_name(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates generate files db table uses app slug and base name."""
        monkeypatch.chdir(tmp_path)
        c = DjangoModelCreator("billing/Invoice")
        c._parse_input()
        c._get_base_model()
        app_dir = tmp_path / "apps" / "billing"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("")
        (app_dir / "serializers.py").write_text("")
        c._generate_files()
        assert "tbl_billing_invoice" in (app_dir / "models.py").read_text()

    def test_generate_files_creates_files_from_scratch(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates generate files creates files from scratch."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        c = DjangoModelCreator("shop/Product")
        c._parse_input()
        c._get_base_model()
        c._generate_files()
        assert (app_dir / "models.py").exists()
        assert (app_dir / "serializers.py").exists()
        assert "class ProductModel(" in (app_dir / "models.py").read_text()
        assert (
            "class ProductModelSerializer(" in (app_dir / "serializers.py").read_text()
        )

    def test_run_returns_early_on_bad_path(self, tmp_path, monkeypatch, capsys):
        """ACCEPTANCE: Validates run returns early on bad path."""
        monkeypatch.chdir(tmp_path)
        DjangoModelCreator("badpath").run()
        assert "[ERROR]" in capsys.readouterr().out

    def test_run_returns_early_when_app_dir_missing(
        self, tmp_path, monkeypatch, capsys
    ):
        """REJECTION: Validates run returns early when app dir missing."""
        monkeypatch.chdir(tmp_path)
        DjangoModelCreator("ghost/Order").run()
        assert "[ERROR]" in capsys.readouterr().out

    def test_run_full_happy_path(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates run full happy path."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("from django.db import models\n")
        (app_dir / "serializers.py").write_text(
            "from rest_framework import serializers\n"
        )
        DjangoModelCreator("shop/Order").run()
        assert "class OrderModel(" in (app_dir / "models.py").read_text()
        assert "class OrderModelSerializer(" in (app_dir / "serializers.py").read_text()

    def test_register_subcommand_wiring(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates register subcommand wiring."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        create_model_module.register_subcommand(subparsers)
        args = parser.parse_args(["createmodel", "shop/Invoice"])
        called = []
        with patch.object(
            DjangoModelCreator, "run", lambda self: called.append(self.model_path)
        ):
            args.handler(args)
        assert called == ["shop/Invoice"]


class TestDjangoModelFieldCreator:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Order", "OrderModel"),
            ("OrderModel", "OrderModel"),
            ("order_item", "OrderItemModel"),
        ],
    )
    def test_format_model_name(self, raw, expected):
        """ACCEPTANCE: Validates format model name."""
        assert (
            DjangoModelFieldCreator("shop/Order", "ref")._format_model_name(raw)
            == expected
        )

    def test_parse_model_path_raises_on_bad_format(self, tmp_path, monkeypatch):
        """REJECTION: Validates parse model path raises on bad format."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="format"):
            DjangoModelFieldCreator("shopOrder", "ref")._parse_model_path()

    def test_parse_model_path_raises_when_models_file_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates parse model path raises when models file missing."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="models.py not found"):
            DjangoModelFieldCreator("shop/Order", "ref")._parse_model_path()

    def test_parse_model_path_raises_when_model_class_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates parse model path raises when model class missing."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            f"class UnrelatedModel(Base):\n    {FIELD_INSERT_MARKER}\n"
        )
        with pytest.raises(ValueError, match="not found"):
            DjangoModelFieldCreator("shop/Order", "ref")._parse_model_path()

    def test_parse_model_path_raises_when_insert_marker_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates parse model path raises when insert marker missing."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("class OrderModel(Base):\n    id = 1\n")
        with pytest.raises(ValueError, match="marker"):
            DjangoModelFieldCreator("shop/Order", "ref")._parse_model_path()

    def test_parse_model_path_succeeds_with_valid_file(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates parse model path succeeds with valid file."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref")
        c._parse_model_path()
        assert c.app_slug == "shop"
        assert c.model_name == "OrderModel"

    def test_get_model_block_returns_correct_bounds(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates get model block returns correct bounds."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref")
        c._parse_model_path()
        lines = c.model_file.read_text().splitlines()
        start, end = c._get_model_block(lines)
        assert lines[start].startswith("class OrderModel(")
        assert end == len(lines)

    def test_get_model_block_ends_before_next_class(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates get model block ends before next class."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            textwrap.dedent(
                f"""\
                class OrderModel(Base):
                    {FIELD_INSERT_MARKER}

                class AnotherModel(Base):
                    pass
                """
            )
        )
        c = DjangoModelFieldCreator("shop/Order", "ref")
        c._parse_model_path()
        lines = c.model_file.read_text().splitlines()
        start, end = c._get_model_block(lines)
        assert not any("AnotherModel" in l for l in lines[start:end])

    def test_get_model_block_raises_when_model_not_found(self, tmp_path, monkeypatch):
        """REJECTION: Validates get model block raises when model not found."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref")
        c._parse_model_path()
        c.model_name = "GhostModel"
        with pytest.raises(ValueError, match="not found"):
            c._get_model_block(c.model_file.read_text().splitlines())

    def test_validate_field_name_blank_is_allowed(self):
        """ACCEPTANCE: Validates validate field name blank is allowed."""
        c = DjangoModelFieldCreator("shop/Order", "")
        c._validate_field_name()
        assert c.field_name == ""

    def test_validate_field_name_appends_ref_suffix(self):
        """ACCEPTANCE: Validates validate field name appends ref suffix."""
        c = DjangoModelFieldCreator("shop/Order", "account")
        c._validate_field_name()
        assert c.field_name == "account_ref"

    def test_validate_field_name_does_not_double_ref(self):
        """ACCEPTANCE: Validates validate field name does not double ref."""
        c = DjangoModelFieldCreator("shop/Order", "account_ref")
        c._validate_field_name()
        assert c.field_name == "account_ref"

    def test_validate_field_name_raises_on_trailing_underscore(self):
        """REJECTION: Validates validate field name raises on trailing underscore."""
        with pytest.raises(ValueError, match="cannot end with"):
            DjangoModelFieldCreator("shop/Order", "bad_")._validate_field_name()

    def test_validate_field_name_raises_on_uppercase(self):
        """REJECTION: Validates validate field name raises on uppercase."""
        with pytest.raises(ValueError, match="Invalid field name"):
            DjangoModelFieldCreator("shop/Order", "BadName")._validate_field_name()

    def test_validate_field_name_raises_on_starting_digit(self):
        """REJECTION: Validates validate field name raises on starting digit."""
        with pytest.raises(ValueError, match="Invalid field name"):
            DjangoModelFieldCreator("shop/Order", "1bad")._validate_field_name()

    def test_validate_foreign_key_raises_when_to_is_none(self, tmp_path, monkeypatch):
        """REJECTION: Validates validate foreign key raises when to is none."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref", to=None)
        c._parse_model_path()
        c._validate_field_name()
        with pytest.raises(ValueError, match="--to is required"):
            c._validate_foreign_key()

    def test_validate_foreign_key_raises_on_bad_to_format(self, tmp_path, monkeypatch):
        """REJECTION: Validates validate foreign key raises on bad to format."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref", to="bad_format")
        c._parse_model_path()
        c._validate_field_name()
        with pytest.raises(ValueError, match="format"):
            c._validate_foreign_key()

    def test_validate_foreign_key_raises_when_parent_app_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates validate foreign key raises when parent app missing."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        c = DjangoModelFieldCreator("shop/Order", "ref", to="ghost/User")
        c._parse_model_path()
        c._validate_field_name()
        with pytest.raises(FileNotFoundError, match="Parent app"):
            c._validate_foreign_key()

    def test_validate_foreign_key_raises_when_parent_model_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates validate foreign key raises when parent model missing."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        _make_model_file(tmp_path / "apps" / "auth", "SomethingElseModel")
        c = DjangoModelFieldCreator("shop/Order", "ref", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        with pytest.raises(ValueError, match="not found"):
            c._validate_foreign_key()

    def test_validate_foreign_key_auto_generates_field_name_when_blank(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates validate foreign key auto generates field name when blank."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        c._validate_foreign_key()
        assert c.field_name == "user_ref"

    def test_validate_foreign_key_raises_on_duplicate_field(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates validate foreign key raises on duplicate field."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            textwrap.dedent(
                f"""\
                class OrderModel(Base):
                    user_ref = models.ForeignKey("auth.UserModel", on_delete=models.CASCADE)
                    {FIELD_INSERT_MARKER}
                """
            )
        )
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "user_ref", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        with pytest.raises(ValueError, match="already exists"):
            c._validate_foreign_key()

    def test_build_foreign_key_field_contains_all_options(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates build foreign key field contains all options."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "user", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        c._validate_foreign_key()
        field = c._build_foreign_key_field()
        assert "models.ForeignKey(" in field
        assert '"auth.UserModel"' in field
        assert "on_delete=models.CASCADE" in field
        assert "related_name=" in field
        assert "db_column='user_ref_id'" in field

    def test_build_foreign_key_field_related_name_format(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates build foreign key field related name format."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "user", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        c._validate_foreign_key()
        assert "shop_order_user_ref_rev" in c._build_foreign_key_field()

    def test_append_field_inserts_before_marker(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates append field inserts before marker."""
        monkeypatch.chdir(tmp_path)
        _make_model_file(tmp_path / "apps" / "shop", "OrderModel")
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "user", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        c._validate_foreign_key()
        c._append_field_to_model(c._build_foreign_key_field())
        content = c.model_file.read_text()
        assert content.index("user_ref = models.ForeignKey") < content.index(
            FIELD_INSERT_MARKER
        )

    def test_append_field_raises_when_marker_not_in_model_block(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates append field raises when marker not in model block."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            "class OrderModel(Base):\n    id = 1\n\n" f"# {FIELD_INSERT_MARKER}\n"
        )
        c = DjangoModelFieldCreator("shop/Order", "user_ref")
        c.app_slug = "shop"
        c.model_name = "OrderModel"
        c.model_file = app_dir / "models.py"
        with pytest.raises(ValueError, match="marker"):
            c._append_field_to_model("user_ref = models.ForeignKey(...)")

    def test_append_field_inserts_blank_line_before_marker_when_prev_not_blank(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates append field inserts blank line before marker when prev not blank."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            textwrap.dedent(
                f"""\
                class OrderModel(Base):
                    id = models.UUIDField()
                    {FIELD_INSERT_MARKER}
                """
            )
        )
        _make_model_file(tmp_path / "apps" / "auth", "UserModel")
        c = DjangoModelFieldCreator("shop/Order", "user", to="auth/User")
        c._parse_model_path()
        c._validate_field_name()
        c._validate_foreign_key()
        c._append_field_to_model(c._build_foreign_key_field())
        lines = c.model_file.read_text().splitlines()
        marker_idx = next(i for i, l in enumerate(lines) if FIELD_INSERT_MARKER in l)
        assert lines[marker_idx - 1].strip() == ""

    def test_register_subcommand_passes_correct_args(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates register subcommand passes correct args."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        create_modelfield_module.register_subcommand(subparsers)
        args = parser.parse_args(
            ["create_model_field", "shop/Order", "user", "--to", "auth/User"]
        )
        called = []
        with patch.object(
            DjangoModelFieldCreator,
            "run",
            lambda self: called.append((self.model_path, self.field_name, self.to)),
        ):
            args.handler(args)
        assert called == [("shop/Order", "user", "auth/User")]


class TestDjangoApiCreatorNormalisation:

    def test_normalize_app_name_prepends_apps(self):
        """ACCEPTANCE: Validates normalize app name prepends apps."""
        assert DjangoApiCreator("shop/x")._normalize_app_name("shop") == "apps.shop"

    def test_normalize_app_name_already_prefixed(self):
        """ACCEPTANCE: Validates normalize app name already prefixed."""
        assert (
            DjangoApiCreator("shop/x")._normalize_app_name("apps.shop") == "apps.shop"
        )

    def test_normalize_app_name_lowercases(self):
        """ACCEPTANCE: Validates normalize app name lowercases."""
        assert DjangoApiCreator("shop/x")._normalize_app_name("SHOP") == "apps.shop"

    def test_normalize_app_name_raises_on_too_many_segments(self):
        """REJECTION: Validates normalize app name raises on too many segments."""
        with pytest.raises(ValueError, match="Invalid App Name"):
            DjangoApiCreator("shop/x")._normalize_app_name("apps.nested.shop")

    def test_normalize_app_name_raises_on_empty_segment(self):
        """REJECTION: Validates normalize app name raises on empty segment."""
        with pytest.raises(ValueError):
            DjangoApiCreator("shop/x")._normalize_app_name("apps.")

    @pytest.mark.parametrize(
        "raw, expected_class",
        [
            ("create_order", "CreateOrderV1APIView"),
            ("order", "OrderV1APIView"),
            ("get_user_profile", "GetUserProfileV1APIView"),
        ],
    )
    def test_normalize_api_name_produces_correct_class(self, raw, expected_class):
        """ACCEPTANCE: Validates normalize api name produces correct class."""
        snake, class_name = DjangoApiCreator("shop/x")._normalize_api_name(raw)
        assert snake == raw
        assert class_name == expected_class

    def test_normalize_api_name_raises_on_uppercase(self):
        """REJECTION: Validates normalize api name raises on uppercase."""
        with pytest.raises(ValueError, match="Invalid API name"):
            DjangoApiCreator("shop/x")._normalize_api_name("CreateOrder")

    def test_normalize_api_name_raises_on_digits(self):
        """REJECTION: Validates normalize api name raises on digits."""
        with pytest.raises(ValueError, match="Invalid API name"):
            DjangoApiCreator("shop/x")._normalize_api_name("order2")

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("create/", "create/"),
            ("/create/", "create/"),
            ("create", "create/"),
            ("user/<int:id>/detail/", "user/<int:id>/detail/"),
        ],
    )
    def test_normalize_url_valid(self, raw, expected):
        """ACCEPTANCE: Validates normalize url valid."""
        assert DjangoApiCreator("shop/x")._normalize_url(raw) == expected

    def test_normalize_url_raises_on_untyped_param(self):
        """REJECTION: Validates normalize url raises on untyped param."""
        with pytest.raises(ValueError, match="format like"):
            DjangoApiCreator("shop/x")._normalize_url("user/<id>/")

    def test_normalize_url_raises_on_invalid_chars(self):
        """REJECTION: Validates normalize url raises on invalid chars."""
        with pytest.raises(ValueError, match="Invalid characters"):
            DjangoApiCreator("shop/x")._normalize_url("bad url/")

    def test_parse_input_sets_all_derived_attributes(self):
        """ACCEPTANCE: Validates parse input sets all derived attributes."""
        c = DjangoApiCreator("shop/create_order")
        c._parse_input()
        assert c.original_app_name == "shop"
        assert c.raw_api == "create_order"
        assert c.api_class_name == "CreateOrderV1APIView"
        assert c.api_function_name == "create_order"
        assert c.api_human_name == "Create Order"
        assert c.api_router_name == "create_order_router"
        assert c.api_router_class_name == "CreateOrderRouter"
        assert c.app == "apps.shop"

    def test_parse_input_raises_on_missing_slash(self):
        """REJECTION: Validates parse input raises on missing slash."""
        with pytest.raises(ValueError, match="format"):
            DjangoApiCreator("shopCreateOrder")._parse_input()

    def test_extract_imports_and_code_separates_correctly(self):
        """ACCEPTANCE: Validates extract imports and code separates correctly."""
        c = DjangoApiCreator("shop/x")
        content = "import os\nfrom pathlib import Path\n\nclass Foo:\n    pass\n"
        imports, code = c._extract_imports_and_code(content)
        assert imports == ["import os", "from pathlib import Path"]
        assert any("class Foo:" in l for l in code)

    def test_extract_imports_and_code_empty_input(self):
        """BOUNDARY: Validates extract imports and code empty input."""
        imports, code = DjangoApiCreator("shop/x")._extract_imports_and_code("")
        assert imports == []
        assert code == []

    def test_find_import_insert_point_after_imports(self):
        """ACCEPTANCE: Validates find import insert point after imports."""
        lines = ["import os", "from pathlib import Path", "", "class Foo:", "    pass"]
        assert DjangoApiCreator("shop/x")._find_import_insert_point(lines) == 3

    def test_find_import_insert_point_returns_zero_when_all_imports(self):
        """BOUNDARY: Validates find import insert point returns zero when all imports."""
        lines = ["import os", "from pathlib import Path"]
        assert DjangoApiCreator("shop/x")._find_import_insert_point(lines) == 0

    def test_find_import_insert_point_returns_zero_on_empty(self):
        """BOUNDARY: Validates find import insert point returns zero on empty."""
        assert DjangoApiCreator("shop/x")._find_import_insert_point([]) == 0

    def test_generate_route_name_returns_expected_name(self):
        """ACCEPTANCE: Validates generate route name returns expected name."""
        c = DjangoApiCreator("shop/create_order")
        c._parse_input()
        assert c._generate_route_name(set(), set()) == "shop__create_order"

    def test_generate_route_name_raises_on_exact_duplicate(self):
        """REJECTION: Validates generate route name raises on exact duplicate."""
        c = DjangoApiCreator("shop/create_order")
        c._parse_input()
        with pytest.raises(FileExistsError, match="already exists"):
            c._generate_route_name({"shop__create_order"}, {"shop__create_order"})

    def test_generate_route_name_warns_on_case_collision(self, capsys):
        """REJECTION: Validates generate route name warns on case collision."""
        c = DjangoApiCreator("shop/create_order")
        c._parse_input()
        c._generate_route_name(set(), {"shop__create_order"})
        assert "WARNING" in capsys.readouterr().out


class TestDjangoApiCreatorFileSteps:

    def test_write_versioned_api_file_creates_new_file(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates write versioned api file creates new file."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(create_api_module, "TEMPLATE_PATH", self._api_tpl(tmp_path)):
            c._write_versioned_api_file()
        api_file = tmp_path / "apps" / "shop" / "apis" / "create_order.py"
        assert api_file.exists()
        content = api_file.read_text()
        assert "class CreateOrderV1APIView(" in content
        assert "Create Order" in content
        assert "shop__create_order" in content

    def test_write_versioned_api_file_raises_when_template_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write versioned api file raises when template missing."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(create_api_module, "TEMPLATE_PATH", tmp_path / "missing.py"):
            with pytest.raises(FileNotFoundError, match="template"):
                c._write_versioned_api_file()

    def test_write_versioned_api_file_raises_if_class_already_exists(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write versioned api file raises if class already exists."""
        c = self._setup_creator(tmp_path, monkeypatch)
        api_dir = tmp_path / "apps" / "shop" / "apis"
        api_dir.mkdir(parents=True)
        (api_dir / "create_order.py").write_text(
            "class CreateOrderV1APIView(APIView): pass\n"
        )
        with patch.object(create_api_module, "TEMPLATE_PATH", self._api_tpl(tmp_path)):
            with pytest.raises(FileExistsError):
                c._write_versioned_api_file()

    def test_write_versioned_api_file_appends_to_existing_file(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates write versioned api file appends to existing file."""
        c = self._setup_creator(tmp_path, monkeypatch)
        api_dir = tmp_path / "apps" / "shop" / "apis"
        api_dir.mkdir(parents=True)
        existing = api_dir / "create_order.py"
        existing.write_text(
            "from rest_framework.views import APIView\n\nclass OtherView(APIView): pass\n"
        )
        with patch.object(create_api_module, "TEMPLATE_PATH", self._api_tpl(tmp_path)):
            c._write_versioned_api_file()
        content = existing.read_text()
        assert "class CreateOrderV1APIView(" in content
        assert "class OtherView(" in content

    def test_write_versioned_api_file_creates_init_in_new_apis_dir(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates write versioned api file creates init in new apis dir."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(create_api_module, "TEMPLATE_PATH", self._api_tpl(tmp_path)):
            c._write_versioned_api_file()
        assert (tmp_path / "apps" / "shop" / "apis" / "__init__.py").exists()

    def test_write_version_router_creates_views_py(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates write version router creates views py."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module,
            "VERSION_ROUTER_TEMPLATE_PATH",
            self._router_tpl(tmp_path),
        ):
            c._write_version_router_to_views()
        views = tmp_path / "apps" / "shop" / "views.py"
        assert views.exists()
        content = views.read_text()
        assert "class CreateOrderRouter(" in content
        assert "create_order_router = CreateOrderRouter()" in content
        assert "1: CreateOrderV1APIView," in content

    def test_write_version_router_raises_when_template_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write version router raises when template missing."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module, "VERSION_ROUTER_TEMPLATE_PATH", tmp_path / "nope.py"
        ):
            with pytest.raises(FileNotFoundError):
                c._write_version_router_to_views()

    def test_write_version_router_raises_if_router_already_exists(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write version router raises if router already exists."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "views.py").write_text("create_order_router = CreateOrderRouter()\n")
        with patch.object(
            create_api_module,
            "VERSION_ROUTER_TEMPLATE_PATH",
            self._router_tpl(tmp_path),
        ):
            with pytest.raises(FileExistsError):
                c._write_version_router_to_views()

    def test_write_version_router_appends_to_existing_views(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates write version router appends to existing views."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "views.py").write_text("# existing\nold_router = OldRouter()\n")
        with patch.object(
            create_api_module,
            "VERSION_ROUTER_TEMPLATE_PATH",
            self._router_tpl(tmp_path),
        ):
            c._write_version_router_to_views()
        content = (app_dir / "views.py").read_text()
        assert "old_router" in content
        assert "create_order_router" in content

    def test_copy_test_template_creates_test_file(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates copy test template creates test file."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module, "TEST_TEMPLATE_PATH", self._test_api_tpl(tmp_path)
        ):
            c._copy_test_template_and_replace()
        test_file = (
            tmp_path / "apps" / "shop" / "tests" / "test_apis" / "test_create_order.py"
        )
        assert test_file.exists()
        content = test_file.read_text()
        assert "class TestCreateOrderV1APIView" in content
        assert "shop__create_order" in content

    def test_copy_test_template_raises_when_template_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates copy test template raises when template missing."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module, "TEST_TEMPLATE_PATH", tmp_path / "nope.py"
        ):
            with pytest.raises(FileNotFoundError):
                c._copy_test_template_and_replace()

    def test_copy_test_template_raises_if_test_class_already_exists(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates copy test template raises if test class already exists."""
        c = self._setup_creator(tmp_path, monkeypatch)
        test_dir = tmp_path / "apps" / "shop" / "tests" / "test_apis"
        test_dir.mkdir(parents=True)
        (test_dir / "test_create_order.py").write_text(
            "class TestCreateOrderV1APIView(object): pass\n"
        )
        with patch.object(
            create_api_module, "TEST_TEMPLATE_PATH", self._test_api_tpl(tmp_path)
        ):
            with pytest.raises(FileExistsError):
                c._copy_test_template_and_replace()

    def test_copy_test_template_appends_to_existing_test_file(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates copy test template appends to existing test file."""
        c = self._setup_creator(tmp_path, monkeypatch)
        test_dir = tmp_path / "apps" / "shop" / "tests" / "test_apis"
        test_dir.mkdir(parents=True)
        (test_dir / "__init__.py").write_text("")
        existing = test_dir / "test_create_order.py"
        existing.write_text("class TestOldView(object): pass\n")
        with patch.object(
            create_api_module, "TEST_TEMPLATE_PATH", self._test_api_tpl(tmp_path)
        ):
            c._copy_test_template_and_replace()
        content = existing.read_text()
        assert "TestCreateOrderV1APIView" in content
        assert "TestOldView" in content

    def test_copy_test_template_creates_init_in_new_test_apis_dir(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates copy test template creates init in new test apis dir."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module, "TEST_TEMPLATE_PATH", self._test_api_tpl(tmp_path)
        ):
            c._copy_test_template_and_replace()
        assert (
            tmp_path / "apps" / "shop" / "tests" / "test_apis" / "__init__.py"
        ).exists()

    def test_write_router_test_creates_test_views_py(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates write router test creates test views py."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module,
            "TEST_ROUTER_TEMPLATE_PATH",
            self._test_router_tpl(tmp_path),
        ):
            c._write_router_test_to_test_views()
        tv = tmp_path / "apps" / "shop" / "tests" / "test_views.py"
        assert tv.exists()
        content = tv.read_text()
        assert "class TestCreateOrderRouter(" in content
        assert "apps.shop.views" in content
        assert "create_order_router" in content
        assert "Create Order" in content

    def test_write_router_test_raises_when_template_missing(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write router test raises when template missing."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with patch.object(
            create_api_module, "TEST_ROUTER_TEMPLATE_PATH", tmp_path / "nope.py"
        ):
            with pytest.raises(FileNotFoundError):
                c._write_router_test_to_test_views()

    def test_write_router_test_raises_if_class_already_exists(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates write router test raises if class already exists."""
        c = self._setup_creator(tmp_path, monkeypatch)
        tests_dir = tmp_path / "apps" / "shop" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_views.py").write_text(
            "class TestCreateOrderRouter(object): pass\n"
        )
        with patch.object(
            create_api_module,
            "TEST_ROUTER_TEMPLATE_PATH",
            self._test_router_tpl(tmp_path),
        ):
            with pytest.raises(FileExistsError):
                c._write_router_test_to_test_views()

    def test_write_router_test_appends_when_class_absent(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates write router test appends when class absent."""
        c = self._setup_creator(tmp_path, monkeypatch)
        tests_dir = tmp_path / "apps" / "shop" / "tests"
        tests_dir.mkdir(parents=True)
        tv = tests_dir / "test_views.py"
        tv.write_text("class TestOldRouter(object): pass\n")
        with patch.object(
            create_api_module,
            "TEST_ROUTER_TEMPLATE_PATH",
            self._test_router_tpl(tmp_path),
        ):
            c._write_router_test_to_test_views()
        content = tv.read_text()
        assert "TestCreateOrderRouter" in content
        assert "TestOldRouter" in content

    def test_update_urls_raises_when_urls_py_missing(self, tmp_path, monkeypatch):
        """REJECTION: Validates update urls raises when urls py missing."""
        c = self._setup_creator(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError, match="urls.py"):
            c._update_urls()

    def test_update_urls_inserts_default_url(self, tmp_path, monkeypatch):
        """BOUNDARY: Validates update urls inserts default url."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text(
            "from django.urls import path\n"
            "from django.views.decorators.csrf import csrf_exempt\n"
            "from . import views\n\nurlpatterns = [\n]\n"
        )
        c._update_urls()
        content = (app_dir / "urls.py").read_text()
        assert "create_order/" in content
        assert "create_order_router" in content
        assert "shop__create_order" in content

    def test_update_urls_inserts_explicit_url_paths(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates update urls inserts explicit url paths."""
        c = self._setup_creator(tmp_path, monkeypatch)
        c.url_paths = ["orders/", "orders/<int:pk>/"]
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text("urlpatterns = [\n]\n")
        c._update_urls()
        content = (app_dir / "urls.py").read_text()
        assert "orders/" in content
        assert "orders/<int:pk>/" in content

    def test_update_urls_raises_on_duplicate_url_pattern(self, tmp_path, monkeypatch):
        """REJECTION: Validates update urls raises on duplicate url pattern."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text(
            "urlpatterns = [\n"
            "    path('create_order/', ..., name='shop__create_order'),\n"
            "]\n"
        )
        with pytest.raises(FileExistsError, match="already exists"):
            c._update_urls()

    def test_update_urls_raises_when_urlpatterns_not_found(self, tmp_path, monkeypatch):
        """REJECTION: Validates update urls raises when urlpatterns not found."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text("# no urlpatterns here\n")
        with pytest.raises(ValueError, match="urlpatterns"):
            c._update_urls()

    def test_update_urls_raises_on_duplicate_route_name(self, tmp_path, monkeypatch):
        """REJECTION: Validates update urls raises on duplicate route name."""
        c = self._setup_creator(tmp_path, monkeypatch)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "urls.py").write_text(
            "urlpatterns = [\n"
            "    path('other/', ..., name='shop__create_order'),\n"
            "]\n"
        )
        with pytest.raises(FileExistsError, match="URL name"):
            c._update_urls()

    def test_register_subcommand_wiring_no_url(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates register subcommand wiring no url."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        create_api_module.register_subcommand(subparsers)
        args = parser.parse_args(["createapi", "shop/create_order"])
        called = []
        with patch.object(
            DjangoApiCreator,
            "run",
            lambda self: called.append((self.api_path, self.url_paths)),
        ):
            args.handler(args)
        assert called == [("shop/create_order", [])]

    def test_register_subcommand_wiring_with_url(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates register subcommand wiring with url."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        create_api_module.register_subcommand(subparsers)
        args = parser.parse_args(
            ["createapi", "shop/create_order", "--url", "orders/", "orders/<int:pk>/"]
        )
        called = []
        with patch.object(
            DjangoApiCreator,
            "run",
            lambda self: called.append(self.url_paths),
        ):
            args.handler(args)
        assert called == [["orders/", "orders/<int:pk>/"]]

    def _setup_creator(self, tmp_path, monkeypatch, api_path="shop/create_order"):
        monkeypatch.chdir(tmp_path)
        c = DjangoApiCreator(api_path)
        c.base_path = tmp_path / "apps"
        c._parse_input()
        return c

    def _api_tpl(self, tmp_path) -> Path:
        tpl = tmp_path / "tpl_api.py"
        tpl.write_text(
            "from rest_framework.views import APIView\n\n"
            "class SampleV1APIView(APIView):\n"
            '    """{{API_HUMAN_NAME}}"""\n'
            "    url_name = '{{API_URL_NAME}}'\n"
            "    def get(self, request): pass\n"
        )
        return tpl

    def _router_tpl(self, tmp_path) -> Path:
        tpl = tmp_path / "tpl_router.py"
        tpl.write_text(
            "from mo_api_kit import VersionRouter\n\n"
            "class SampleRouterClassName(VersionRouter):\n"
            '    """{{API_HUMAN_NAME}}"""\n'
            "    versions = {mo_api_kit.__str__}\n\n"
            "sample_router_function_name = SampleRouterClassName()\n"
        )
        return tpl

    def _test_api_tpl(self, tmp_path) -> Path:
        tpl = tmp_path / "tpl_test_api.py"
        tpl.write_text(
            "import pytest\n\n"
            "class SampleV1APIViewTest:\n"
            "    url_name = '{{API_URL_NAME}}'\n"
            "    def test_placeholder(self): pass\n"
        )
        return tpl

    def _test_router_tpl(self, tmp_path) -> Path:
        tpl = tmp_path / "tpl_test_router.py"
        tpl.write_text(
            "import apps.sample_app_name.views as views_module\n\n"
            "class SampleRouterTestClassName(object):\n"
            '    """Sample Api Human Name"""\n'
            "    router = views_module.sample_router_function_name\n"
            "    def test_router(self): pass\n"
        )
        return tpl


class TestDjangoAppDeleter:

    def test_init_prepends_apps_prefix_when_missing(self, tmp_path, monkeypatch):
        """REJECTION: Validates init prepends apps prefix when missing."""
        monkeypatch.chdir(tmp_path)
        d = DjangoAppDeleter("billing")
        assert d.dotted_path == "apps.billing"
        assert d.original_path == "apps.billing"

    def test_init_does_not_double_prepend_prefix(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates init does not double prepend prefix."""
        monkeypatch.chdir(tmp_path)
        d = DjangoAppDeleter("apps.billing")
        assert d.dotted_path == "apps.billing"

    def test_init_lowercases_app_segment(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates init lowercases app segment."""
        monkeypatch.chdir(tmp_path)
        d = DjangoAppDeleter("Billing")
        assert d.dotted_path == "apps.billing"
        assert d.app_name == "billing"

    def test_normalize_path_raises_on_too_many_segments(self, tmp_path, monkeypatch):
        """REJECTION: Validates normalize path raises on too many segments."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Invalid App Name"):
            DjangoAppDeleter("apps.nested.billing")

    def test_normalize_path_raises_on_empty_segment(self, tmp_path, monkeypatch):
        """REJECTION: Validates normalize path raises on empty segment."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            DjangoAppDeleter("apps.")

    def test_app_dir_uses_dotted_path(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates app dir uses dotted path."""
        monkeypatch.chdir(tmp_path)
        d = DjangoAppDeleter("shop")
        assert d.app_dir == str(tmp_path / "apps" / "shop")

    def test_confirm_deletion_returns_true_on_y(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates confirm deletion returns true on y."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert DjangoAppDeleter("shop")._confirm_deletion() is True

    @pytest.mark.parametrize("answer", ["n", "N", "yes", "", "no", " "])
    def test_confirm_deletion_returns_false_on_non_y(
        self, answer, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates confirm deletion returns false on non y."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert DjangoAppDeleter("shop")._confirm_deletion() is False

    def test_confirm_deletion_strips_whitespace(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates confirm deletion strips whitespace."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: "  y  ")
        assert DjangoAppDeleter("shop")._confirm_deletion() is True

    def test_delete_app_dir_removes_directory(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates delete app dir removes directory."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("# content")
        d = DjangoAppDeleter("shop")
        d._delete_app_dir()
        assert not app_dir.exists()

    def test_delete_app_dir_prints_confirmation(self, tmp_path, monkeypatch, capsys):
        """ACCEPTANCE: Validates delete app dir prints confirmation."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        DjangoAppDeleter("shop")._delete_app_dir()
        assert "[OK]" in capsys.readouterr().out

    def test_clean_empty_parent_dirs_removes_empty_intermediate_dir(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates clean empty parent dirs removes empty intermediate dir."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "group" / "shop"
        app_dir.mkdir(parents=True)
        d = DjangoAppDeleter("shop")
        d.app_dir = str(app_dir)
        d.src_dir = str(tmp_path / "apps")
        app_dir.rmdir()
        d._clean_empty_parent_dirs()
        assert not (tmp_path / "apps" / "group").exists()

    def test_clean_empty_parent_dirs_removes_init_file_in_empty_dir(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates clean empty parent dirs removes init file in empty dir."""
        monkeypatch.chdir(tmp_path)
        intermediate = tmp_path / "apps" / "group"
        intermediate.mkdir(parents=True)
        init = intermediate / "__init__.py"
        init.write_text("")
        d = DjangoAppDeleter("shop")
        d.app_dir = str(intermediate / "shop")
        d.src_dir = str(tmp_path / "apps")
        d._clean_empty_parent_dirs()
        assert not init.exists()
        assert not intermediate.exists()

    def test_clean_empty_parent_dirs_stops_at_src_dir(self, tmp_path, monkeypatch):
        """BOUNDARY: Validates clean empty parent dirs stops at src dir."""
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir(parents=True)
        d = DjangoAppDeleter("shop")
        d.app_dir = str(apps_dir / "shop")
        d.src_dir = str(apps_dir)
        d._clean_empty_parent_dirs()
        assert apps_dir.exists()

    def test_clean_empty_parent_dirs_stops_when_parent_not_empty(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates clean empty parent dirs stops when parent not empty."""
        monkeypatch.chdir(tmp_path)
        intermediate = tmp_path / "apps" / "group"
        sibling = intermediate / "other_app"
        sibling.mkdir(parents=True)
        d = DjangoAppDeleter("shop")
        d.app_dir = str(intermediate / "shop")
        d.src_dir = str(tmp_path / "apps")
        d._clean_empty_parent_dirs()
        assert intermediate.exists()

    def test_clean_empty_parent_dirs_silent_on_exception(self, tmp_path, monkeypatch):
        """BOUNDARY: Validates clean empty parent dirs silent on exception."""
        monkeypatch.chdir(tmp_path)
        d = DjangoAppDeleter("shop")
        d.app_dir = str(tmp_path / "apps" / "nonexistent" / "shop")
        d.src_dir = str(tmp_path / "apps")
        d._clean_empty_parent_dirs()

    def test_remove_from_settings_deletes_matching_line(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates remove from settings deletes matching line."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        (config / "settings.py").write_text(
            "INSTALLED_APPS = [\n"
            "    'django.contrib.auth',\n"
            "    'apps.shop',\n"
            "]\n"
        )
        DjangoAppDeleter("shop")._remove_from_settings()
        content = (config / "settings.py").read_text()
        assert "apps.shop" not in content
        assert "django.contrib.auth" in content

    def test_remove_from_settings_skips_write_when_not_present(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates remove from settings skips write when not present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        original = "INSTALLED_APPS = [\n    'django.contrib.auth',\n]\n"
        (config / "settings.py").write_text(original)
        DjangoAppDeleter("shop")._remove_from_settings()
        assert (config / "settings.py").read_text() == original

    def test_remove_from_settings_prints_ok_when_removed(
        self, tmp_path, monkeypatch, capsys
    ):
        """ACCEPTANCE: Validates remove from settings prints ok when removed."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        (config / "settings.py").write_text("INSTALLED_APPS = [\n    'apps.shop',\n]\n")
        DjangoAppDeleter("shop")._remove_from_settings()
        assert "[OK]" in capsys.readouterr().out

    def test_remove_from_urls_deletes_matching_route(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates remove from urls deletes matching route."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        route = "    path('v<int:version>/shop/', include('apps.shop.urls')),"
        (config / "urls.py").write_text(
            f"urlpatterns = [\n{route}\n    path('other/', include('other.urls')),\n]\n"
        )
        DjangoAppDeleter("shop")._remove_from_urls()
        content = (config / "urls.py").read_text()
        assert "apps.shop.urls" not in content
        assert "other.urls" in content

    def test_remove_from_urls_rewrites_even_when_route_absent(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates remove from urls rewrites even when route absent."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        original = "urlpatterns = [\n    path('other/', include('other.urls')),\n]\n"
        (config / "urls.py").write_text(original)
        DjangoAppDeleter("shop")._remove_from_urls()
        assert "other.urls" in (config / "urls.py").read_text()

    def test_remove_from_urls_prints_ok(self, tmp_path, monkeypatch, capsys):
        """ACCEPTANCE: Validates remove from urls prints ok."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        (config / "urls.py").write_text("urlpatterns = [\n]\n")
        DjangoAppDeleter("shop")._remove_from_urls()
        assert "[OK]" in capsys.readouterr().out

    def test_run_prints_error_when_app_dir_missing(self, tmp_path, monkeypatch, capsys):
        """REJECTION: Validates run prints error when app dir missing."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: "y")
        DjangoAppDeleter("ghost").run()
        assert "[ERROR]" in capsys.readouterr().out

    def test_run_cancels_when_confirmation_declined(
        self, tmp_path, monkeypatch, capsys
    ):
        """ACCEPTANCE: Validates run cancels when confirmation declined."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "apps" / "shop").mkdir(parents=True)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        DjangoAppDeleter("shop").run()
        assert (tmp_path / "apps" / "shop").exists()
        assert "cancelled" in capsys.readouterr().out.lower()

    def test_run_calls_all_steps_in_order(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates run calls all steps in order."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "apps" / "shop").mkdir(parents=True)
        monkeypatch.setattr("builtins.input", lambda _: "y")
        d = DjangoAppDeleter("shop")
        order = []
        monkeypatch.setattr(d, "_delete_app_dir", lambda: order.append("delete_dir"))
        monkeypatch.setattr(
            d, "_clean_empty_parent_dirs", lambda: order.append("clean_parents")
        )
        monkeypatch.setattr(
            d, "_remove_from_settings", lambda: order.append("settings")
        )
        monkeypatch.setattr(d, "_remove_from_urls", lambda: order.append("urls"))
        d.run()
        assert order == ["delete_dir", "clean_parents", "settings", "urls"]

    def test_run_full_happy_path(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates run full happy path."""
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        config = tmp_path / "config"
        config.mkdir()
        (config / "settings.py").write_text(
            "INSTALLED_APPS = [\n    'django.contrib.auth',\n    'apps.shop',\n]\n"
        )
        route = "    path('v<int:version>/shop/', include('apps.shop.urls')),"
        (config / "urls.py").write_text(f"urlpatterns = [\n{route}\n]\n")
        monkeypatch.setattr("builtins.input", lambda _: "y")
        DjangoAppDeleter("shop").run()
        assert not app_dir.exists()
        assert "apps.shop" not in (config / "settings.py").read_text()
        assert "apps.shop.urls" not in (config / "urls.py").read_text()

    def test_register_subcommand_calls_deleter_for_each_app(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates register subcommand calls deleter for each app."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        delete_app_module.register_subcommand(subparsers)
        args = parser.parse_args(["deleteapp", "shop", "billing"])
        deleted = []
        with patch.object(
            DjangoAppDeleter, "run", lambda self: deleted.append(self.app_name)
        ):
            args.handler(args)
        assert deleted == ["shop", "billing"]

    def test_register_subcommand_single_app(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates register subcommand single app."""
        monkeypatch.chdir(tmp_path)
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        delete_app_module.register_subcommand(subparsers)
        args = parser.parse_args(["deleteapp", "finance"])
        deleted = []
        with patch.object(
            DjangoAppDeleter, "run", lambda self: deleted.append(self.app_name)
        ):
            args.handler(args)
        assert deleted == ["finance"]


def _build_parser(module, command_name):
    parser = argparse.ArgumentParser(prog="mindoff.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    module.register_subcommand(subparsers)
    argv = [command_name]
    if command_name == "createapp":
        argv.append("placeholder_app")
    return parser.parse_args(argv)


def _make_model_file(app_dir: Path, model_name: str) -> Path:
    app_dir.mkdir(parents=True, exist_ok=True)
    model_file = app_dir / "models.py"
    model_file.write_text(
        textwrap.dedent(
            f"""\
            from django.db import models

            class {model_name}(mindoff_models.TimeStampModel):
                id = models.UUIDField(primary_key=True)
                {FIELD_INSERT_MARKER}

                class Meta:
                    db_table = 'tbl_test'
            """
        )
    )
    return model_file
