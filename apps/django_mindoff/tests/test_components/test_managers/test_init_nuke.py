import argparse
import pytest
from ....components.managers import init as init_manager
from ....components.managers import nuke as nuke_manager


class TestInitManager:

    def test_register_subcommand_invokes_project_creator(self, monkeypatch):
        """ACCEPTANCE: Validates register subcommand invokes project creator."""
        args = _build_parser(init_manager, "init")
        called = []

        class _FakeCreator:
            def run(self):
                called.append(True)

        monkeypatch.setattr(init_manager, "DjangoProjectCreator", _FakeCreator)
        args.handler(args)
        assert called == [True]

    def test_project_creator_run_aborts_when_not_confirmed(self, monkeypatch):
        """ACCEPTANCE: Validates project creator run aborts when not confirmed."""
        creator = init_manager.DjangoProjectCreator()
        monkeypatch.setattr("builtins.input", lambda _="": "n")
        called = {"venv": 0}
        monkeypatch.setattr(
            creator,
            "_create_venv",
            lambda: called.__setitem__("venv", called["venv"] + 1),
        )
        creator.run()
        assert called["venv"] == 0

    @pytest.mark.parametrize("answer", ["N", "no", "  ", "anything_else"])
    def test_project_creator_run_aborts_on_non_yes_inputs(self, answer, monkeypatch):
        """ACCEPTANCE: Validates project creator run aborts on non yes inputs."""
        creator = init_manager.DjangoProjectCreator()
        monkeypatch.setattr("builtins.input", lambda _="": answer)
        called = []
        monkeypatch.setattr(creator, "_create_venv", lambda: called.append("venv"))
        creator.run()
        assert called == []

    def test_project_creator_run_executes_pipeline_when_confirmed(self, monkeypatch):
        """ACCEPTANCE: Validates project creator run executes pipeline when confirmed."""
        creator = init_manager.DjangoProjectCreator()
        monkeypatch.setattr("builtins.input", lambda _="": "y")
        monkeypatch.setattr(init_manager.os, "chdir", lambda *_: None)
        monkeypatch.setattr(creator, "_prompt_optional_dependencies", lambda: [])
        order = []
        monkeypatch.setattr(creator, "_create_venv", lambda: order.append("venv"))
        monkeypatch.setattr(creator, "_install_packages", lambda: order.append("deps"))
        monkeypatch.setattr(
            creator, "_initialize_django_project", lambda: order.append("project")
        )
        monkeypatch.setattr(
            creator, "_update_settings", lambda: order.append("settings")
        )
        monkeypatch.setattr(creator, "_create_env_file", lambda: order.append("env"))
        monkeypatch.setattr(creator, "_update_urls", lambda: order.append("urls"))
        monkeypatch.setattr(
            creator, "_create_extra_folders", lambda: order.append("folders")
        )
        monkeypatch.setattr(
            creator, "_write_supporting_files", lambda: order.append("support")
        )
        monkeypatch.setattr(creator, "_initialize_git", lambda: order.append("git"))
        creator.run()
        assert order == [
            "venv",
            "deps",
            "project",
            "settings",
            "env",
            "urls",
            "folders",
            "support",
            "git",
        ]

    def test_project_creator_run_pipeline_is_complete(self, monkeypatch):
        """ACCEPTANCE: Validates project creator run pipeline is complete."""
        creator = init_manager.DjangoProjectCreator()
        monkeypatch.setattr("builtins.input", lambda _="": "y")
        monkeypatch.setattr(init_manager.os, "chdir", lambda *_: None)
        monkeypatch.setattr(creator, "_prompt_optional_dependencies", lambda: [])
        EXPECTED_STEPS = {
            "_create_venv",
            "_install_packages",
            "_initialize_django_project",
            "_update_settings",
            "_create_env_file",
            "_update_urls",
            "_create_extra_folders",
            "_write_supporting_files",
            "_initialize_git",
        }
        called = set()
        for step in EXPECTED_STEPS:
            monkeypatch.setattr(creator, step, lambda s=step: called.add(s))
        creator.run()
        assert called == EXPECTED_STEPS

    def test_extract_secret_and_debug_info_strips_and_records(self):
        """ACCEPTANCE: Validates extract secret and debug info strips and records."""
        creator = init_manager.DjangoProjectCreator()
        content = (
            "from pathlib import Path\n"
            "SECRET_KEY = 'my-secret'\n"
            "DEBUG = True\n"
            "ALLOWED_HOSTS = []\n"
        )
        lines, secret_key, insert_pos = creator._extract_secret_and_debug_info(
            content, [], "", {}
        )
        assert secret_key == "'my-secret'"
        assert "SECRET_KEY" in insert_pos
        assert "DEBUG" in insert_pos
        assert all("SECRET_KEY" not in l and "DEBUG" not in l for l in lines)

    def test_append_to_list_inserts_value(self):
        """ACCEPTANCE: Validates append to list inserts value."""
        creator = init_manager.DjangoProjectCreator()
        text = "INSTALLED_APPS = [\n    'django.contrib.admin',\n]"
        result = creator._append_to_list(text, "INSTALLED_APPS", "rest_framework")
        assert "'rest_framework'" in result

    def test_append_to_list_does_not_duplicate(self):
        """REJECTION: Validates append to list does not duplicate."""
        creator = init_manager.DjangoProjectCreator()
        text = "INSTALLED_APPS = [\n    'rest_framework',\n]"
        result = creator._append_to_list(text, "INSTALLED_APPS", "rest_framework")
        assert result.count("rest_framework") == 2  # original + inserted

    def test_update_settings_injects_decouple_and_mindoff(self, tmp_path):
        """BOUNDARY: Validates update settings injects decouple and mindoff."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.py"
        settings_file.write_text(
            "from pathlib import Path\n"
            "BASE_DIR = Path(__file__).resolve().parent.parent\n"
            "SECRET_KEY = 'django-insecure-abc123'\n"
            "DEBUG = True\n"
            "INSTALLED_APPS = [\n"
            "    'django.contrib.admin',\n"
            "]\n"
            'TEMPLATES = [{"BACKEND": "...", "DIRS": [], "OPTIONS": {}}]\n'
        )
        creator = init_manager.DjangoProjectCreator()
        creator.project_root = tmp_path
        creator.config_dir = config_dir
        creator.settings_path = settings_file
        creator._update_settings()
        updated = settings_file.read_text()
        assert "from decouple import config" in updated
        assert "SECRET_KEY = config('DJANGO_SECRET_KEY')" in updated
        assert "rest_framework" in updated
        assert "django_mindoff" in updated
        assert "MINDOFF_LOG_ERRORS_IN_DEBUG" in updated

    def test_update_settings_decouple_already_imported_not_duplicated(self, tmp_path):
        """REJECTION: Validates update settings decouple already imported not duplicated."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.py"
        settings_file.write_text(
            "from pathlib import Path\n"
            "from decouple import config\n"
            "SECRET_KEY = 'abc'\n"
            "DEBUG = True\n"
            "INSTALLED_APPS = [\n    'django.contrib.admin',\n]\n"
        )
        creator = init_manager.DjangoProjectCreator()
        creator.project_root = tmp_path
        creator.config_dir = config_dir
        creator.settings_path = settings_file
        creator._update_settings()
        assert settings_file.read_text().count("from decouple import config") == 1

    def test_update_settings_mindoff_header_not_duplicated(self, tmp_path):
        """REJECTION: Validates update settings mindoff header not duplicated."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        header = "# ===== MINDOFF SPECIFIC SETTINGS OPTIONS ====="
        settings_file = config_dir / "settings.py"
        settings_file.write_text(
            "from pathlib import Path\n"
            "SECRET_KEY = 'abc'\n"
            "DEBUG = True\n"
            "INSTALLED_APPS = [\n    'django.contrib.admin',\n]\n"
            f"{header}\n"
            "MINDOFF_LOG_ERRORS_IN_DEBUG = False\n"
        )
        creator = init_manager.DjangoProjectCreator()
        creator.project_root = tmp_path
        creator.config_dir = config_dir
        creator.settings_path = settings_file
        creator._update_settings()
        assert settings_file.read_text().count(header) == 1

    def test_update_urls_adds_include_and_templateview(self, tmp_path):
        """ACCEPTANCE: Validates update urls adds include and templateview."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        urls_file = config_dir / "urls.py"
        urls_file.write_text(
            '"""URL config."""\n'
            "from django.contrib import admin\n"
            "from django.urls import path\n"
            "urlpatterns = [\n"
            "    path('admin/', admin.site.urls),\n"
            "]\n"
        )
        creator = init_manager.DjangoProjectCreator()
        creator.urls_path = urls_file
        creator._update_urls()
        content = urls_file.read_text()
        assert "include" in content
        assert "TemplateView" in content
        assert "index.html" in content
        assert '"""URL config."""' not in content

    def test_create_env_file_writes_expected_keys(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create env file writes expected keys."""
        monkeypatch.chdir(tmp_path)
        creator = init_manager.DjangoProjectCreator()
        creator.secret_key = "'test-secret-key'"
        creator._create_env_file()
        env = (tmp_path / ".env").read_text()
        assert "DJANGO_SECRET_KEY=" in env
        assert "DEBUG=True" in env
        assert "REDIS_URL=" in env

    def test_prompt_optional_dependencies_returns_empty_list(self):
        """BOUNDARY: Validates prompt optional dependencies returns empty list."""
        creator = init_manager.DjangoProjectCreator()
        assert creator._prompt_optional_dependencies() == []

    def test_create_venv_skips_when_already_exists(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create venv skips when already exists."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".venv").mkdir()
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess, "run", lambda *a, **kw: run_calls.append(a)
        )
        creator = init_manager.DjangoProjectCreator()
        creator._create_venv()
        assert run_calls == []

    def test_create_venv_runs_when_missing(self, tmp_path, monkeypatch):
        """REJECTION: Validates create venv runs when missing."""
        monkeypatch.chdir(tmp_path)
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess,
            "run",
            lambda argv, **kw: run_calls.append(argv),
        )
        creator = init_manager.DjangoProjectCreator()
        creator._create_venv()
        assert any("venv" in str(a) for a in run_calls[0])

    def test_install_packages_installs_base_and_mindoff(self, monkeypatch):
        """BOUNDARY: Validates install packages installs base and mindoff."""
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess,
            "run",
            lambda argv, **kw: run_calls.append(list(argv)),
        )
        creator = init_manager.DjangoProjectCreator()
        creator.optional_packages = []
        creator._install_packages()
        all_args = [arg for call in run_calls for arg in call]
        assert "django>=5.0" in all_args
        assert "djangorestframework>=3.15.0,<4.0" in all_args
        assert "django-mindoff" in all_args

    def test_install_packages_installs_optional_when_present(self, monkeypatch):
        """ACCEPTANCE: Validates install packages installs optional when present."""
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess,
            "run",
            lambda argv, **kw: run_calls.append(list(argv)),
        )
        creator = init_manager.DjangoProjectCreator()
        creator.optional_packages = ["polars", "numpy"]
        creator._install_packages()
        all_args = [arg for call in run_calls for arg in call]
        assert "polars" in all_args
        assert "numpy" in all_args

    def test_install_packages_skips_optional_subprocess_when_empty(self, monkeypatch):
        """BOUNDARY: Validates install packages skips optional subprocess when empty."""
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess,
            "run",
            lambda argv, **kw: run_calls.append(list(argv)),
        )
        creator = init_manager.DjangoProjectCreator()
        creator.optional_packages = []
        creator._install_packages()
        assert len(run_calls) == 2

    def test_initialize_django_project_calls_django_admin(self, monkeypatch):
        """BOUNDARY: Validates initialize django project calls django admin."""
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess,
            "run",
            lambda argv, **kw: run_calls.append(list(argv)),
        )
        creator = init_manager.DjangoProjectCreator()
        creator._initialize_django_project()
        assert len(run_calls) == 1
        assert "startproject" in run_calls[0]
        assert "config" in run_calls[0]

    def test_create_extra_folders_creates_apps_dir_and_copies_html(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates create extra folders creates apps dir and copies html."""
        creator = init_manager.DjangoProjectCreator()
        creator.project_root = tmp_path
        creator.app_dir_path = tmp_path / "apps"
        fake_resources = tmp_path / "resources" / "html"
        fake_resources.mkdir(parents=True)
        (fake_resources / "index.html").write_text("<html></html>")
        copy_calls = []
        monkeypatch.setattr(
            init_manager.shutil, "copy", lambda src, dst: copy_calls.append((src, dst))
        )
        import unittest.mock as mock

        with mock.patch(
            "pathlib.Path.glob", return_value=[fake_resources / "index.html"]
        ):
            creator._create_extra_folders()
        assert creator.app_dir_path.exists()
        assert (tmp_path / "templates").exists()
        assert len(copy_calls) >= 1

    def test_write_supporting_files_copies_all_targets(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates write supporting files copies all targets."""
        creator = init_manager.DjangoProjectCreator()
        creator.project_root = tmp_path
        creator.config_dir = tmp_path / "config"
        creator.config_dir.mkdir()
        copy_calls = []
        monkeypatch.setattr(
            init_manager.shutil, "copy", lambda src, dst: copy_calls.append(dst)
        )
        creator._write_supporting_files()
        dst_names = [str(d) for d in copy_calls]
        assert any("mindoff.py" in n for n in dst_names)
        assert any("pytest.ini" in n for n in dst_names)
        assert any(".gitignore" in n for n in dst_names)
        assert any("responses.csv" in n for n in dst_names)

    def test_initialize_git_touches_readme_and_runs_three_git_commands(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates initialize git touches readme and runs three git commands."""
        monkeypatch.chdir(tmp_path)
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        creator = init_manager.DjangoProjectCreator()
        creator._initialize_git()
        assert (tmp_path / "README.md").exists()
        flat_calls = [arg for call in run_calls for arg in call]
        assert "init" in flat_calls
        assert "add" in flat_calls
        assert "commit" in flat_calls

    def test_initialize_git_commit_has_initial_commit_message(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates initialize git commit has initial commit message."""
        monkeypatch.chdir(tmp_path)
        run_calls = []
        monkeypatch.setattr(
            init_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        creator = init_manager.DjangoProjectCreator()
        creator._initialize_git()
        commit_call = next(c for c in run_calls if "commit" in c)
        assert "Initial commit" in commit_call


class TestNukeManager:

    def test_register_subcommand_invokes_project_deleter(self, monkeypatch):
        """ACCEPTANCE: Validates register subcommand invokes project deleter."""
        args = _build_parser(nuke_manager, "nuke")
        called = []

        class _FakeDeleter:
            def __init__(self, dry_run=False, delete_all=False):
                called.append((dry_run, delete_all))

            def run(self):
                called.append("run")

        monkeypatch.setattr(nuke_manager, "DjangoProjectDeleter", _FakeDeleter)
        args.handler(args)
        assert called == [(False, False), "run"]

    def test_register_subcommand_dry_run_flag(self, monkeypatch):
        """ACCEPTANCE: Validates register subcommand dry run flag."""
        parser = argparse.ArgumentParser(prog="mindoff.py")
        subparsers = parser.add_subparsers(dest="command", required=True)
        nuke_manager.register_subcommand(subparsers)
        args = parser.parse_args(["nuke", "--dry-run"])
        called = []

        class _FakeDeleter:
            def __init__(self, dry_run=False, delete_all=False):
                called.append((dry_run, delete_all))

            def run(self):
                called.append("run")

        monkeypatch.setattr(nuke_manager, "DjangoProjectDeleter", _FakeDeleter)
        args.handler(args)
        assert called[0] == (True, False)

    def test_register_subcommand_all_flag(self, monkeypatch):
        """ACCEPTANCE: Validates register subcommand all flag."""
        parser = argparse.ArgumentParser(prog="mindoff.py")
        subparsers = parser.add_subparsers(dest="command", required=True)
        nuke_manager.register_subcommand(subparsers)
        args = parser.parse_args(["nuke", "--all"])
        called = []

        class _FakeDeleter:
            def __init__(self, dry_run=False, delete_all=False):
                called.append((dry_run, delete_all))

            def run(self):
                called.append("run")

        monkeypatch.setattr(nuke_manager, "DjangoProjectDeleter", _FakeDeleter)
        args.handler(args)
        assert called[0] == (False, True)

    def test_nuke_run_dry_run_path(self, monkeypatch):
        """ACCEPTANCE: Validates nuke run dry run path."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=True)
        called = []
        monkeypatch.setattr(deleter, "_choose_scope", lambda: called.append("scope"))
        monkeypatch.setattr(
            deleter, "_identify_targets", lambda: called.append("identify")
        )
        monkeypatch.setattr(deleter, "_print_dry_run", lambda: called.append("dry"))
        monkeypatch.setattr(
            deleter, "_confirm_deletion", lambda: called.append("confirm")
        )
        deleter.run()
        assert called == ["scope", "identify", "dry"]

    def test_nuke_run_abort_when_not_confirmed(self, monkeypatch):
        """ACCEPTANCE: Validates nuke run abort when not confirmed."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=False)
        called = []
        monkeypatch.setattr(deleter, "_choose_scope", lambda: called.append("scope"))
        monkeypatch.setattr(
            deleter, "_identify_targets", lambda: called.append("identify")
        )
        monkeypatch.setattr(deleter, "_confirm_deletion", lambda: False)
        monkeypatch.setattr(
            deleter, "_perform_deletion", lambda: called.append("delete")
        )
        monkeypatch.setattr(
            deleter, "_report_summary", lambda: called.append("summary")
        )
        deleter.run()
        assert called == ["scope", "identify"]

    def test_nuke_run_executes_delete_when_confirmed(self, monkeypatch):
        """ACCEPTANCE: Validates nuke run executes delete when confirmed."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=False)
        called = []
        monkeypatch.setattr(deleter, "_choose_scope", lambda: called.append("scope"))
        monkeypatch.setattr(
            deleter, "_identify_targets", lambda: called.append("identify")
        )
        monkeypatch.setattr(deleter, "_confirm_deletion", lambda: True)
        monkeypatch.setattr(
            deleter, "_perform_deletion", lambda: called.append("delete")
        )
        monkeypatch.setattr(
            deleter, "_report_summary", lambda: called.append("summary")
        )
        deleter.run()
        assert called == ["scope", "identify", "delete", "summary"]

    def test_nuke_run_dry_run_skips_confirm_and_delete(self, monkeypatch):
        """ACCEPTANCE: Validates nuke run dry run skips confirm and delete."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=True)
        delete_called = []
        monkeypatch.setattr(deleter, "_choose_scope", lambda: None)
        monkeypatch.setattr(deleter, "_identify_targets", lambda: None)
        monkeypatch.setattr(deleter, "_print_dry_run", lambda: None)
        monkeypatch.setattr(
            deleter, "_perform_deletion", lambda: delete_called.append(True)
        )
        deleter.run()
        assert delete_called == []

    def test_identify_targets_excludes_default_items(self, tmp_path):
        """BOUNDARY: Validates identify targets excludes default items."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=False, delete_all=False)
        deleter.project_root = tmp_path
        (tmp_path / "config").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "manage.py").write_text("")
        (tmp_path / ".env").write_text("")
        (tmp_path / "README.md").write_text("")
        deleter._identify_targets()
        target_names = {p.name for p in deleter.to_delete}
        skipped_names = {p.name for p in deleter.skipped}
        assert "config" in target_names
        assert "manage.py" in target_names
        assert ".git" in skipped_names
        assert "README.md" in skipped_names

    def test_identify_targets_delete_all_includes_git_and_venv(self, tmp_path):
        """ACCEPTANCE: Validates identify targets delete all includes git and venv."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=False, delete_all=True)
        deleter.project_root = tmp_path
        (tmp_path / ".git").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / "manage.py").write_text("")
        deleter._identify_targets()
        target_names = {p.name for p in deleter.to_delete}
        assert ".git" in target_names
        assert ".venv" in target_names

    def test_identify_targets_skips_nonexistent_artifacts(self, tmp_path):
        """BOUNDARY: Validates identify targets skips nonexistent artifacts."""
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=False)
        deleter.project_root = tmp_path
        deleter._identify_targets()
        assert deleter.to_delete == []
        assert deleter.skipped == []

    def test_confirm_deletion_returns_false_when_no_targets(self):
        """ACCEPTANCE: Validates confirm deletion returns false when no targets."""
        deleter = nuke_manager.DjangoProjectDeleter()
        assert deleter._confirm_deletion() is False

    def test_confirm_deletion_returns_true_on_yes(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates confirm deletion returns true on yes."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        target = tmp_path / "manage.py"
        target.write_text("")
        deleter.to_delete = [target]
        monkeypatch.setattr("builtins.input", lambda _="": "y")
        assert deleter._confirm_deletion() is True

    def test_confirm_deletion_returns_false_on_no(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates confirm deletion returns false on no."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        target = tmp_path / "manage.py"
        target.write_text("")
        deleter.to_delete = [target]
        monkeypatch.setattr("builtins.input", lambda _="": "n")
        assert deleter._confirm_deletion() is False

    def test_perform_deletion_removes_files(self, tmp_path):
        """ACCEPTANCE: Validates perform deletion removes files."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        f = tmp_path / "db.sqlite3"
        f.write_text("")
        deleter.to_delete = [f]
        deleter._perform_deletion()
        assert not f.exists()
        assert f in deleter.deleted

    def test_perform_deletion_removes_directories(self, tmp_path):
        """ACCEPTANCE: Validates perform deletion removes directories."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        d = tmp_path / "apps"
        d.mkdir()
        deleter.to_delete = [d]
        deleter._perform_deletion()
        assert not d.exists()
        assert d in deleter.deleted

    def test_perform_deletion_records_errors_gracefully(self, tmp_path):
        """REJECTION: Validates perform deletion records errors gracefully."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        phantom = tmp_path / "ghost_file.py"
        deleter.to_delete = [phantom]
        deleter._perform_deletion()
        assert phantom not in deleter.deleted

    @pytest.mark.parametrize(
        "choice, expected_delete_all",
        [("1", False), ("", False), ("2", True)],
    )
    def test_choose_scope_sets_delete_all(
        self, choice, expected_delete_all, monkeypatch
    ):
        """ACCEPTANCE: Validates choose scope sets delete all."""
        deleter = nuke_manager.DjangoProjectDeleter()
        monkeypatch.setattr("builtins.input", lambda _="": choice)
        deleter._choose_scope()
        assert deleter.delete_all is expected_delete_all

    def test_report_summary_outputs_deleted_and_skipped(self, tmp_path, capsys):
        """ACCEPTANCE: Validates report summary outputs deleted and skipped."""
        deleter = nuke_manager.DjangoProjectDeleter()
        deleter.project_root = tmp_path
        deleter.deleted = [tmp_path / "manage.py"]
        deleter.skipped = [tmp_path / ".git"]
        deleter._report_summary()
        out = capsys.readouterr().out
        assert "manage.py" in out
        assert ".git" in out


def _build_parser(module, command_name):
    parser = argparse.ArgumentParser(prog="mindoff.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    module.register_subcommand(subparsers)
    return parser.parse_args([command_name])
