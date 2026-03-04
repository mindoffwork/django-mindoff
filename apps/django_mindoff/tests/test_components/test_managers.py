import argparse
import sys

import pytest

from ...components.managers import _create as create_manager
from ...components.managers import _delete as delete_manager
from ...components.managers import init as init_manager
from ...components.managers import nuke as nuke_manager


def _build_parser(module, command_name):
    parser = argparse.ArgumentParser(prog="mindoff.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    module.register_subcommand(subparsers)
    return parser.parse_args([command_name])


class TestCreateManager:
    """Unit tests for interactive create manager wrapper command."""

    def test_create_app_command_runs_once_and_returns(self):
        args = _build_parser(create_manager, "create")

        with pytest.MonkeyPatch.context() as m:
            choose_count = {"n": 0}

            def _choose_once():
                choose_count["n"] += 1
                return "createapp"

            m.setattr(create_manager, "_choose_create_command", _choose_once)
            m.setattr(create_manager, "_create_app_flow", lambda: ["sample_app"])
            calls = []
            m.setattr(
                create_manager,
                "_run_create_command",
                lambda command, argv: calls.append((command, argv)),
            )

            args.handler(args)

        assert choose_count["n"] == 1
        assert calls == [("createapp", ["sample_app"])]

    def test_repeating_flow_same_app_then_exit(self):
        with pytest.MonkeyPatch.context() as m:
            m.setattr(create_manager, "_get_local_apps", lambda: ["app_one"])
            build_calls = []

            def _fake_build(command, local_apps, selected_app):
                build_calls.append((command, tuple(local_apps), selected_app))
                return ["app_one/user_api"], "app_one"

            m.setattr(create_manager, "_build_create_args", _fake_build)
            run_calls = []
            m.setattr(
                create_manager,
                "_run_create_command",
                lambda command, argv: run_calls.append((command, list(argv))),
            )
            actions = iter(["same_app", "exit"])
            m.setattr(create_manager, "_choose_post_create_action", lambda: next(actions))

            should_exit = create_manager._run_repeating_create_flow("createapi")

        assert should_exit is True
        assert build_calls == [
            ("createapi", ("app_one",), None),
            ("createapi", ("app_one",), "app_one"),
        ]
        assert run_calls == [
            ("createapi", ["app_one/user_api"]),
            ("createapi", ["app_one/user_api"]),
        ]

    def test_repeating_flow_different_app_resets_selected_app(self):
        with pytest.MonkeyPatch.context() as m:
            apps_calls = []

            def _fake_get_local_apps():
                apps_calls.append(True)
                return ["app_one", "app_two"]

            m.setattr(create_manager, "_get_local_apps", _fake_get_local_apps)
            build_calls = []

            def _fake_build(command, local_apps, selected_app):
                build_calls.append((command, tuple(local_apps), selected_app))
                return ["app_one/Order"], "app_one"

            m.setattr(create_manager, "_build_create_args", _fake_build)
            m.setattr(create_manager, "_run_create_command", lambda *_: None)
            actions = iter(["different_app", "exit"])
            m.setattr(create_manager, "_choose_post_create_action", lambda: next(actions))

            should_exit = create_manager._run_repeating_create_flow("createmodel")

        assert should_exit is True
        assert len(apps_calls) == 2
        assert build_calls == [
            ("createmodel", ("app_one", "app_two"), None),
            ("createmodel", ("app_one", "app_two"), None),
        ]

    def test_repeating_flow_main_menu_returns_false(self):
        with pytest.MonkeyPatch.context() as m:
            m.setattr(create_manager, "_get_local_apps", lambda: ["app_one"])
            m.setattr(
                create_manager,
                "_build_create_args",
                lambda *_: (["app_one/Invoice"], "app_one"),
            )
            m.setattr(create_manager, "_run_create_command", lambda *_: None)
            m.setattr(create_manager, "_choose_post_create_action", lambda: "main_menu")

            should_exit = create_manager._run_repeating_create_flow("createmodel")

        assert should_exit is False

    def test_create_handler_main_menu_then_create_app(self):
        args = _build_parser(create_manager, "create")

        with pytest.MonkeyPatch.context() as m:
            choices = iter(["createmodel", "createapp"])
            m.setattr(create_manager, "_choose_create_command", lambda: next(choices))
            m.setattr(create_manager, "_run_repeating_create_flow", lambda *_: False)
            m.setattr(create_manager, "_create_app_flow", lambda: ["finance"])
            run_calls = []
            m.setattr(
                create_manager,
                "_run_create_command",
                lambda command, argv: run_calls.append((command, argv)),
            )

            args.handler(args)

        assert run_calls == [("createapp", ["finance"])]

    @pytest.mark.parametrize(
        "raw_input, expected",
        [
            ("1", "same_app"),
            ("2", "different_app"),
            ("3", "main_menu"),
            ("4", "exit"),
        ],
    )
    def test_post_create_menu_choices(self, raw_input, expected, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": raw_input)
        assert create_manager._choose_post_create_action() == expected

    def test_post_create_menu_defaults_to_same_app_on_blank(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "")
        assert create_manager._choose_post_create_action() == "same_app"

    def test_choose_create_command_retries_until_valid(self, monkeypatch):
        entered = iter(["bad", "4"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entered))
        assert create_manager._choose_create_command() == "createapi"

    def test_run_create_command_createmodel_runs_per_model(self, monkeypatch):
        run_calls = []
        monkeypatch.setattr(
            create_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )
        create_manager._run_create_command("createmodel", ["app/One", "app/Two"])
        assert run_calls == [
            [sys.executable, "mindoff.py", "createmodel", "app/One"],
            [sys.executable, "mindoff.py", "createmodel", "app/Two"],
        ]

    def test_repeating_flow_returns_false_when_args_empty(self):
        with pytest.MonkeyPatch.context() as m:
            m.setattr(create_manager, "_get_local_apps", lambda: ["app_one"])
            m.setattr(create_manager, "_build_create_args", lambda *_: ([], None))
            should_exit = create_manager._run_repeating_create_flow("createapi")
        assert should_exit is False


class TestDeleteManager:
    """Unit tests for interactive delete manager wrapper command."""

    def test_delete_subcommand_accepts_label_and_calls_delete_flow(self, monkeypatch):
        args = _build_parser(delete_manager, "delete")

        monkeypatch.setattr("builtins.input", lambda _="": "app")
        called = []
        monkeypatch.setattr(
            delete_manager,
            "_delete_apps_via_subprocess",
            lambda: called.append(True),
        )

        args.handler(args)
        assert called == [True]

    def test_delete_apps_via_subprocess_uses_deduplicated_names(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        (apps_dir / "alpha").mkdir(parents=True)
        (apps_dir / "beta").mkdir(parents=True)
        (apps_dir / "alpha" / "__init__.py").write_text("")
        (apps_dir / "beta" / "__init__.py").write_text("")

        monkeypatch.setattr("builtins.input", lambda _="": "alpha beta beta bad")
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )

        delete_manager._delete_apps_via_subprocess()

        assert run_calls == [
            [sys.executable, "mindoff.py", "deleteapp", "alpha", "beta"]
        ]

    def test_delete_apps_via_subprocess_retries_when_no_valid_selection(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        (apps_dir / "alpha").mkdir(parents=True)
        (apps_dir / "alpha" / "__init__.py").write_text("")

        entered = iter(["bad", "alpha"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entered))

        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )

        delete_manager._delete_apps_via_subprocess()

        assert run_calls == [[sys.executable, "mindoff.py", "deleteapp", "alpha"]]

    def test_delete_apps_no_apps_directory_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )
        delete_manager._delete_apps_via_subprocess()
        assert run_calls == []

    def test_delete_apps_no_valid_app_dirs_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        (apps_dir / "not_an_app").mkdir(parents=True)
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )
        delete_manager._delete_apps_via_subprocess()
        assert run_calls == []


class TestInitManager:
    """Unit tests for init manager command registration and flow wiring."""

    def test_register_subcommand_invokes_project_creator(self, monkeypatch):
        args = _build_parser(init_manager, "init")
        called = []

        class _FakeCreator:
            def run(self):
                called.append(True)

        monkeypatch.setattr(init_manager, "DjangoProjectCreator", _FakeCreator)
        args.handler(args)
        assert called == [True]

    def test_project_creator_run_aborts_when_not_confirmed(self, monkeypatch):
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

    def test_project_creator_run_executes_pipeline_when_confirmed(self, monkeypatch):
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
        monkeypatch.setattr(creator, "_update_settings", lambda: order.append("settings"))
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


class TestNukeManager:
    """Unit tests for nuke manager command registration and run branches."""

    def test_register_subcommand_invokes_project_deleter(self, monkeypatch):
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

    def test_nuke_run_dry_run_path(self, monkeypatch):
        deleter = nuke_manager.DjangoProjectDeleter(dry_run=True)
        called = []
        monkeypatch.setattr(deleter, "_choose_scope", lambda: called.append("scope"))
        monkeypatch.setattr(
            deleter, "_identify_targets", lambda: called.append("identify")
        )
        monkeypatch.setattr(deleter, "_print_dry_run", lambda: called.append("dry"))
        monkeypatch.setattr(deleter, "_confirm_deletion", lambda: called.append("confirm"))
        deleter.run()
        assert called == ["scope", "identify", "dry"]

    def test_nuke_run_abort_when_not_confirmed(self, monkeypatch):
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
        monkeypatch.setattr(deleter, "_report_summary", lambda: called.append("summary"))
        deleter.run()
        assert called == ["scope", "identify"]

    def test_nuke_run_executes_delete_when_confirmed(self, monkeypatch):
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
        monkeypatch.setattr(deleter, "_report_summary", lambda: called.append("summary"))
        deleter.run()
        assert called == ["scope", "identify", "delete", "summary"]
