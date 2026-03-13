import argparse
import sys
import pytest
from ....components.managers import create as create_manager
from ....components.managers import delete as delete_manager


class TestCreateManager:

    def test_create_app_command_runs_once_and_returns(self):
        """ACCEPTANCE: Validates create app command runs once and returns."""
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

    def test_create_handler_main_menu_then_create_app(self):
        """ACCEPTANCE: Validates create handler main menu then create app."""
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

    def test_create_handler_exits_after_repeating_flow_returns_true(self):
        """ACCEPTANCE: Validates create handler exits after repeating flow returns true."""
        args = _build_parser(create_manager, "create")

        with pytest.MonkeyPatch.context() as m:
            m.setattr(create_manager, "_choose_create_command", lambda: "createmodel")
            m.setattr(create_manager, "_run_repeating_create_flow", lambda *_: True)
            run_calls = []
            m.setattr(
                create_manager,
                "_run_create_command",
                lambda *_: run_calls.append(True),
            )
            args.handler(args)

        assert run_calls == []

    def test_create_handler_create_model_field_delegates_to_repeating_flow(self):
        """ACCEPTANCE: Validates create handler create model field delegates to repeating flow."""
        args = _build_parser(create_manager, "create")

        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                create_manager, "_choose_create_command", lambda: "create_model_field"
            )
            flow_calls = []
            m.setattr(
                create_manager,
                "_run_repeating_create_flow",
                lambda cmd: flow_calls.append(cmd) or True,
            )
            args.handler(args)

        assert flow_calls == ["create_model_field"]

    def test_repeating_flow_same_app_then_exit(self):
        """ACCEPTANCE: Validates repeating flow same app then exit."""
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
            m.setattr(
                create_manager, "_choose_post_create_action", lambda: next(actions)
            )

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
        """ACCEPTANCE: Validates repeating flow different app resets selected app."""
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
            m.setattr(
                create_manager, "_choose_post_create_action", lambda: next(actions)
            )

            should_exit = create_manager._run_repeating_create_flow("createmodel")

        assert should_exit is True
        assert len(apps_calls) == 2
        assert build_calls == [
            ("createmodel", ("app_one", "app_two"), None),
            ("createmodel", ("app_one", "app_two"), None),
        ]

    def test_repeating_flow_main_menu_returns_false(self):
        """ACCEPTANCE: Validates repeating flow main menu returns false."""
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

    def test_repeating_flow_returns_false_when_args_empty(self):
        """BOUNDARY: Validates repeating flow returns false when args empty."""
        with pytest.MonkeyPatch.context() as m:
            m.setattr(create_manager, "_get_local_apps", lambda: ["app_one"])
            m.setattr(create_manager, "_build_create_args", lambda *_: ([], None))
            should_exit = create_manager._run_repeating_create_flow("createapi")
        assert should_exit is False

    def test_build_create_args_routes_createmodel(self):
        """ACCEPTANCE: Validates build create args routes createmodel."""
        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                create_manager,
                "_create_model_flow",
                lambda apps, sel: (["app/MyModel"], "app"),
            )
            result = create_manager._build_create_args("createmodel", ["app"], None)
        assert result == (["app/MyModel"], "app")

    def test_build_create_args_routes_createapi(self):
        """ACCEPTANCE: Validates build create args routes createapi."""
        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                create_manager,
                "_create_api_flow",
                lambda apps, sel: (["app/my_api"], "app"),
            )
            result = create_manager._build_create_args("createapi", ["app"], None)
        assert result == (["app/my_api"], "app")

    def test_build_create_args_routes_create_model_field(self):
        """ACCEPTANCE: Validates build create args routes create model field."""
        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                create_manager,
                "_create_model_field_flow",
                lambda apps, sel: (["app/MyModel", "field_ref"], "app"),
            )
            result = create_manager._build_create_args(
                "create_model_field", ["app"], None
            )
        assert result == (["app/MyModel", "field_ref"], "app")

    def test_run_create_command_createmodel_runs_per_model(self, monkeypatch):
        """ACCEPTANCE: Validates run create command createmodel runs per model."""
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

    def test_run_create_command_createapi_single_subprocess_call(self, monkeypatch):
        """ACCEPTANCE: Validates run create command createapi single subprocess call."""
        run_calls = []
        monkeypatch.setattr(
            create_manager.subprocess,
            "run",
            lambda argv: run_calls.append(argv),
        )
        create_manager._run_create_command(
            "createapi", ["app/my_api", "--url", "items/"]
        )
        assert run_calls == [
            [sys.executable, "mindoff.py", "createapi", "app/my_api", "--url", "items/"]
        ]

    def test_run_create_command_noop_on_empty_args(self, monkeypatch):
        """BOUNDARY: Validates run create command noop on empty args."""
        run_calls = []
        monkeypatch.setattr(
            create_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        create_manager._run_create_command("createmodel", [])
        assert run_calls == []

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
        """ACCEPTANCE: Validates post create menu choices."""
        monkeypatch.setattr("builtins.input", lambda _="": raw_input)
        assert create_manager._choose_post_create_action() == expected

    def test_post_create_menu_defaults_to_same_app_on_blank(self, monkeypatch):
        """BOUNDARY: Validates post create menu defaults to same app on blank."""
        monkeypatch.setattr("builtins.input", lambda _="": "")
        assert create_manager._choose_post_create_action() == "same_app"

    def test_post_create_menu_retries_on_invalid_then_accepts(self, monkeypatch):
        """REJECTION: Validates post create menu retries on invalid then accepts."""
        entered = iter(["99", "bad", "2"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entered))
        assert create_manager._choose_post_create_action() == "different_app"

    def test_choose_create_command_retries_until_valid(self, monkeypatch):
        """ACCEPTANCE: Validates choose create command retries until valid."""
        entered = iter(["bad", "4"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entered))
        assert create_manager._choose_create_command() == "create_model_field"

    @pytest.mark.parametrize(
        "choice, expected",
        [
            ("1", "createapp"),
            ("2", "createapi"),
            ("3", "createmodel"),
            ("4", "create_model_field"),
        ],
    )
    def test_choose_create_command_numeric_options(self, choice, expected, monkeypatch):
        """ACCEPTANCE: Validates choose create command numeric options."""
        monkeypatch.setattr("builtins.input", lambda _="": choice)
        assert create_manager._choose_create_command() == expected

    def test_get_local_apps_returns_valid_apps(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates get local apps returns valid apps."""
        monkeypatch.chdir(tmp_path)
        apps = tmp_path / "apps"
        for name in ("good_app", "no_init_app", "no_models_app"):
            (apps / name).mkdir(parents=True)
        (apps / "good_app" / "__init__.py").write_text("")
        (apps / "good_app" / "models.py").write_text("")
        (apps / "no_init_app" / "models.py").write_text("")
        (apps / "no_models_app" / "__init__.py").write_text("")

        result = create_manager._get_local_apps()
        assert result == ["good_app"]

    def test_get_local_apps_apps_py_also_qualifies(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates get local apps apps py also qualifies."""
        monkeypatch.chdir(tmp_path)
        apps = tmp_path / "apps"
        (apps / "my_app").mkdir(parents=True)
        (apps / "my_app" / "__init__.py").write_text("")
        (apps / "my_app" / "apps.py").write_text("")

        result = create_manager._get_local_apps()
        assert "my_app" in result


class TestCreateManagerFlows:

    def test_create_app_flow_returns_split_names(self, monkeypatch):
        """ACCEPTANCE: Validates create app flow returns split names."""
        monkeypatch.setattr("builtins.input", lambda _="": "shop orders")
        assert create_manager._create_app_flow() == ["shop", "orders"]

    def test_create_app_flow_empty_input_returns_empty_list(self, monkeypatch):
        """BOUNDARY: Validates create app flow empty input returns empty list."""
        monkeypatch.setattr("builtins.input", lambda _="": "")
        assert create_manager._create_app_flow() == []

    def test_create_app_flow_single_app(self, monkeypatch):
        """ACCEPTANCE: Validates create app flow single app."""
        monkeypatch.setattr("builtins.input", lambda _="": "billing")
        assert create_manager._create_app_flow() == ["billing"]

    def test_create_model_flow_no_apps_returns_empty(self):
        """BOUNDARY: Validates create model flow no apps returns empty."""
        result, app = create_manager._create_model_flow([])
        assert result == []
        assert app is None

    def test_create_model_flow_no_app_chosen_returns_empty(self, monkeypatch):
        """BOUNDARY: Validates create model flow no app chosen returns empty."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: None)
        result, app = create_manager._create_model_flow(["myapp"])
        assert result == []
        assert app is None

    def test_create_model_flow_single_valid_model(self, monkeypatch):
        """ACCEPTANCE: Validates create model flow single valid model."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "myapp")
        monkeypatch.setattr("builtins.input", lambda _="": "Invoice")
        result, app = create_manager._create_model_flow(["myapp"])
        assert result == ["myapp/Invoice"]
        assert app == "myapp"

    def test_create_model_flow_multiple_models(self, monkeypatch):
        """ACCEPTANCE: Validates create model flow multiple models."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        monkeypatch.setattr("builtins.input", lambda _="": "Order LineItem")
        result, app = create_manager._create_model_flow(["shop"])
        assert result == ["shop/Order", "shop/LineItem"]
        assert app == "shop"

    def test_create_model_flow_retries_on_invalid_name(self, monkeypatch):
        """REJECTION: Validates create model flow retries on invalid name."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        inputs = iter(["bad_name", "lowercase", "GoodModel"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        result, _ = create_manager._create_model_flow(["shop"])
        assert result == ["shop/GoodModel"]

    def test_create_model_flow_retries_on_empty_input(self, monkeypatch):
        """BOUNDARY: Validates create model flow retries on empty input."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        inputs = iter(["", "MyModel"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        result, _ = create_manager._create_model_flow(["shop"])
        assert result == ["shop/MyModel"]

    def test_create_model_flow_passes_selected_app(self, monkeypatch):
        """ACCEPTANCE: Validates create model flow passes selected app."""
        chosen = []

        def _fake_choose(apps, sel):
            chosen.append(sel)
            return sel

        monkeypatch.setattr(create_manager, "_choose_app", _fake_choose)
        monkeypatch.setattr("builtins.input", lambda _="": "MyModel")
        create_manager._create_model_flow(["billing"], selected_app="billing")
        assert chosen == ["billing"]

    def test_create_api_flow_no_apps_returns_empty(self):
        """BOUNDARY: Validates create api flow no apps returns empty."""
        result, app = create_manager._create_api_flow([])
        assert result == []
        assert app is None

    def test_create_api_flow_no_app_chosen_returns_empty(self, monkeypatch):
        """BOUNDARY: Validates create api flow no app chosen returns empty."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: None)
        result, app = create_manager._create_api_flow(["myapp"])
        assert result == []
        assert app is None

    def test_create_api_flow_valid_name_no_url(self, monkeypatch):
        """ACCEPTANCE: Validates create api flow valid name no url."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        inputs = iter(["user_profile", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        result, app = create_manager._create_api_flow(["shop"])
        assert result == ["shop/user_profile"]
        assert app == "shop"

    def test_create_api_flow_with_urls(self, monkeypatch):
        """ACCEPTANCE: Validates create api flow with urls."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        inputs = iter(["order_detail", "orders/ orders/<int:id>/"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        result, _ = create_manager._create_api_flow(["shop"])
        assert "--url" in result
        assert "orders/" in result
        assert "orders/<int:id>/" in result

    def test_create_api_flow_retries_on_invalid_name(self, monkeypatch):
        """REJECTION: Validates create api flow retries on invalid name."""
        monkeypatch.setattr(create_manager, "_choose_app", lambda apps, sel: "shop")
        inputs = iter(["BadName", "123bad", "good_api", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        result, _ = create_manager._create_api_flow(["shop"])
        assert result == ["shop/good_api"]

    def test_create_model_field_flow_no_model_found_returns_empty(self, monkeypatch):
        """BOUNDARY: Validates create model field flow no model found returns empty."""
        monkeypatch.setattr(
            create_manager,
            "_choose_a_existing_model",
            lambda apps, sel: ([], None, None),
        )
        result, app = create_manager._create_model_field_flow(["myapp"])
        assert result == []
        assert app is None

    def test_create_model_field_flow_happy_path(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates create model field flow happy path."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("class Order(models.Model):\n    pass\n")

        monkeypatch.setattr(
            create_manager,
            "_choose_a_existing_model",
            lambda apps, sel: (["shop/Order", "shop/LineItem"], "shop/Order", "shop"),
        )
        monkeypatch.setattr(
            create_manager,
            "_resolve_foreign_key_field_name",
            lambda text, cls: "account",
        )
        monkeypatch.setattr(
            create_manager,
            "_choose_from_list",
            lambda prompt, items, **kw: items[0],
        )
        result, app = create_manager._create_model_field_flow(["shop"])
        assert app == "shop"
        assert "shop/Order" in result
        assert "account" in result
        assert result == ["shop/Order", "account", "--to", "shop/Order"]

    def test_choose_app_returns_selected_app_immediately(self):
        """ACCEPTANCE: Validates choose app returns selected app immediately."""
        result = create_manager._choose_app(
            ["app_one", "app_two"], selected_app="app_two"
        )
        assert result == "app_two"

    def test_choose_app_loops_until_valid(self, monkeypatch):
        """ACCEPTANCE: Validates choose app loops until valid."""
        call_count = {"n": 0}

        def _fake_choose(prompt, items):
            call_count["n"] += 1
            return None if call_count["n"] < 2 else "app_one"

        monkeypatch.setattr(create_manager, "_choose_from_list", _fake_choose)
        result = create_manager._choose_app(["app_one"])
        assert result == "app_one"
        assert call_count["n"] == 2

    def test_choose_from_list_by_number(self, monkeypatch):
        """ACCEPTANCE: Validates choose from list by number."""
        monkeypatch.setattr("builtins.input", lambda _="": "2")
        assert (
            create_manager._choose_from_list("Pick:", ["alpha", "beta", "gamma"])
            == "beta"
        )

    def test_choose_from_list_by_name(self, monkeypatch):
        """ACCEPTANCE: Validates choose from list by name."""
        monkeypatch.setattr("builtins.input", lambda _="": "gamma")
        assert (
            create_manager._choose_from_list("Pick:", ["alpha", "beta", "gamma"])
            == "gamma"
        )

    def test_choose_from_list_out_of_range_returns_none(self, monkeypatch):
        """BOUNDARY: Validates choose from list out of range returns none."""
        monkeypatch.setattr("builtins.input", lambda _="": "99")
        assert create_manager._choose_from_list("Pick:", ["alpha"]) is None

    def test_choose_from_list_zero_returns_none(self, monkeypatch):
        """BOUNDARY: Validates choose from list zero returns none."""
        monkeypatch.setattr("builtins.input", lambda _="": "0")
        assert create_manager._choose_from_list("Pick:", ["alpha"]) is None

    def test_choose_from_list_unknown_string_returns_none(self, monkeypatch):
        """REJECTION: Validates choose from list unknown string returns none."""
        monkeypatch.setattr("builtins.input", lambda _="": "nope")
        assert create_manager._choose_from_list("Pick:", ["alpha", "beta"]) is None

    def test_choose_from_list_first_item(self, monkeypatch):
        """ACCEPTANCE: Validates choose from list first item."""
        monkeypatch.setattr("builtins.input", lambda _="": "1")
        assert create_manager._choose_from_list("Pick:", ["only"]) == "only"

    def test_choose_a_existing_model_no_models_returns_empty(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates choose a existing model no models returns empty."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        (tmp_path / "apps" / "empty_app").mkdir(parents=True)
        (tmp_path / "apps" / "empty_app" / "models.py").write_text("# no classes\n")
        result, model, app = create_manager._choose_a_existing_model(["empty_app"])
        assert result == []
        assert model is None
        assert app is None

    def test_choose_a_existing_model_finds_classes(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates choose a existing model finds classes."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        app_dir = tmp_path / "apps" / "billing"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text(
            "class Invoice(models.Model):\n    pass\n"
            "class LineItem(models.Model):\n    pass\n"
        )
        monkeypatch.setattr(
            create_manager, "_choose_from_list", lambda prompt, items, **kw: items[0]
        )
        models, model, app = create_manager._choose_a_existing_model(["billing"])
        assert "billing/Invoice" in models
        assert "billing/LineItem" in models
        assert model == "billing/Invoice"
        assert app == "billing"

    def test_choose_a_existing_model_filters_by_selected_app(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates choose a existing model filters by selected app."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        for app_name, model_name in [("shop", "Order"), ("billing", "Invoice")]:
            d = tmp_path / "apps" / app_name
            d.mkdir(parents=True)
            (d / "models.py").write_text(
                f"class {model_name}(models.Model):\n    pass\n"
            )

        monkeypatch.setattr(
            create_manager, "_choose_from_list", lambda prompt, items, **kw: items[0]
        )
        models, model, _ = create_manager._choose_a_existing_model(
            ["shop", "billing"], selected_app="billing"
        )
        assert all("billing" in m for m in models)
        assert "shop" not in model

    def test_choose_a_existing_model_retries_on_bad_selection(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates choose a existing model retries on bad selection."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        app_dir = tmp_path / "apps" / "shop"
        app_dir.mkdir(parents=True)
        (app_dir / "models.py").write_text("class Order(models.Model):\n    pass\n")

        call_count = {"n": 0}

        def _fake_choose(prompt, items, **kw):
            call_count["n"] += 1
            return None if call_count["n"] < 2 else items[0]

        monkeypatch.setattr(create_manager, "_choose_from_list", _fake_choose)
        _, model, _ = create_manager._choose_a_existing_model(["shop"])
        assert model == "shop/Order"
        assert call_count["n"] == 2

    def test_choose_a_existing_model_no_models_file(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates choose a existing model no models file."""
        monkeypatch.setattr(create_manager, "apps_folder", tmp_path / "apps")
        (tmp_path / "apps" / "bare_app").mkdir(parents=True)
        result, model, _ = create_manager._choose_a_existing_model(["bare_app"])
        assert result == []
        assert model is None

    def test_resolve_fk_field_blank_returns_blank(self, monkeypatch):
        """ACCEPTANCE: Validates resolve fk field blank returns blank."""
        monkeypatch.setattr("builtins.input", lambda _="": "")
        assert create_manager._resolve_foreign_key_field_name("", "MyModel") == ""

    def test_resolve_fk_field_valid_first_try(self, monkeypatch):
        """ACCEPTANCE: Validates resolve fk field valid first try."""
        monkeypatch.setattr("builtins.input", lambda _="": "account")
        assert (
            create_manager._resolve_foreign_key_field_name("", "MyModel") == "account"
        )

    def test_resolve_fk_field_rejects_invalid_format(self, monkeypatch):
        """REJECTION: Validates resolve fk field rejects invalid format."""
        inputs = iter(["BadName", "ends_", "good_field"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        assert (
            create_manager._resolve_foreign_key_field_name("", "MyModel")
            == "good_field"
        )

    def test_resolve_fk_field_rejects_duplicate_in_model(self, monkeypatch):
        """REJECTION: Validates resolve fk field rejects duplicate in model."""
        model_text = "    account_ref = models.ForeignKey(...)\n"
        inputs = iter(["account", "other_account"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        assert (
            create_manager._resolve_foreign_key_field_name(model_text, "MyModel")
            == "other_account"
        )

    def test_resolve_fk_field_already_ends_ref_not_doubled(self, monkeypatch):
        """ACCEPTANCE: Validates resolve fk field already ends ref not doubled."""
        monkeypatch.setattr("builtins.input", lambda _="": "owner_ref")
        assert (
            create_manager._resolve_foreign_key_field_name("", "MyModel") == "owner_ref"
        )

    def test_normalise_fk_adds_ref_suffix(self):
        """ACCEPTANCE: Validates normalise fk adds ref suffix."""
        assert (
            create_manager._normalise_foreign_key_field_name("account") == "account_ref"
        )

    def test_normalise_fk_does_not_double_ref(self):
        """ACCEPTANCE: Validates normalise fk does not double ref."""
        assert (
            create_manager._normalise_foreign_key_field_name("account_ref")
            == "account_ref"
        )


class TestDeleteManager:

    def test_delete_subcommand_accepts_label_and_calls_delete_flow(self, monkeypatch):
        """ACCEPTANCE: Validates delete subcommand accepts label and calls delete flow."""
        args = _build_parser(delete_manager, "delete")
        monkeypatch.setattr("builtins.input", lambda _="": "1")
        called = []
        monkeypatch.setattr(
            delete_manager,
            "_delete_apps_via_subprocess",
            lambda: called.append(True),
        )
        args.handler(args)
        assert called == [True]

    def test_delete_handler_retries_on_invalid_command(self, monkeypatch):
        """REJECTION: Validates delete handler retries on invalid command."""
        args = _build_parser(delete_manager, "delete")
        inputs = iter(["9", "bad_cmd", "1"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        called = []
        monkeypatch.setattr(
            delete_manager,
            "_delete_apps_via_subprocess",
            lambda: called.append(True),
        )
        args.handler(args)
        assert called == [True]

    def test_delete_apps_via_subprocess_uses_deduplicated_names(
        self, tmp_path, monkeypatch
    ):
        """REJECTION: Validates delete apps via subprocess uses deduplicated names."""
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
        """ACCEPTANCE: Validates delete apps via subprocess retries when no valid selection."""
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
        """ACCEPTANCE: Validates delete apps no apps directory exits."""
        monkeypatch.chdir(tmp_path)
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        delete_manager._delete_apps_via_subprocess()
        assert run_calls == []

    def test_delete_apps_no_valid_app_dirs_exits(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates delete apps no valid app dirs exits."""
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        (apps_dir / "not_an_app").mkdir(parents=True)
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        delete_manager._delete_apps_via_subprocess()
        assert run_calls == []

    def test_delete_apps_numeric_selection(self, tmp_path, monkeypatch):
        """ACCEPTANCE: Validates delete apps numeric selection."""
        monkeypatch.chdir(tmp_path)
        apps_dir = tmp_path / "apps"
        (apps_dir / "alpha").mkdir(parents=True)
        (apps_dir / "alpha" / "__init__.py").write_text("")

        monkeypatch.setattr("builtins.input", lambda _="": "1")
        run_calls = []
        monkeypatch.setattr(
            delete_manager.subprocess, "run", lambda argv: run_calls.append(argv)
        )
        delete_manager._delete_apps_via_subprocess()
        assert run_calls == [[sys.executable, "mindoff.py", "deleteapp", "alpha"]]

    def test_get_valid_app_names_returns_none_when_no_apps_dir(
        self, tmp_path, monkeypatch
    ):
        """BOUNDARY: Validates get valid app names returns none when no apps dir."""
        monkeypatch.chdir(tmp_path)
        assert delete_manager._get_valid_app_names() is None

    def test_get_valid_app_names_excludes_dirs_without_init(
        self, tmp_path, monkeypatch
    ):
        """ACCEPTANCE: Validates get valid app names excludes dirs without init."""
        monkeypatch.chdir(tmp_path)
        apps = tmp_path / "apps"
        (apps / "has_init").mkdir(parents=True)
        (apps / "no_init").mkdir(parents=True)
        (apps / "has_init" / "__init__.py").write_text("")

        result = delete_manager._get_valid_app_names()
        assert result == ["has_init"]

    def test_parse_app_choices_by_name(self):
        """ACCEPTANCE: Validates parse app choices by name."""
        assert delete_manager._parse_app_choices(["alpha"], ["alpha", "beta"]) == [
            "alpha"
        ]

    def test_parse_app_choices_by_number(self):
        """ACCEPTANCE: Validates parse app choices by number."""
        assert delete_manager._parse_app_choices(["2"], ["alpha", "beta"]) == ["beta"]

    def test_parse_app_choices_deduplicates(self):
        """REJECTION: Validates parse app choices deduplicates."""
        assert delete_manager._parse_app_choices(["1", "alpha"], ["alpha", "beta"]) == [
            "alpha"
        ]

    def test_parse_app_choices_ignores_invalid(self):
        """REJECTION: Validates parse app choices ignores invalid."""
        assert delete_manager._parse_app_choices(["99", "invalid", "1"], ["alpha"]) == [
            "alpha"
        ]

    def test_parse_app_choices_out_of_range_ignored(self):
        """ACCEPTANCE: Validates parse app choices out of range ignored."""
        assert delete_manager._parse_app_choices(["0", "3"], ["alpha", "beta"]) == []

    def test_resolve_delete_command_by_number(self):
        """ACCEPTANCE: Validates resolve delete command by number."""
        options = {"1": ("app", "deleteapp")}
        assert delete_manager._resolve_delete_command("1", options) == "deleteapp"

    def test_resolve_delete_command_by_label(self):
        """ACCEPTANCE: Validates resolve delete command by label."""
        options = {"1": ("app", "deleteapp")}
        assert delete_manager._resolve_delete_command("app", options) == "deleteapp"

    def test_resolve_delete_command_unknown_returns_none(self):
        """REJECTION: Validates resolve delete command unknown returns none."""
        options = {"1": ("app", "deleteapp")}
        assert delete_manager._resolve_delete_command("unknown", options) is None


def _build_parser(module, command_name):
    parser = argparse.ArgumentParser(prog="mindoff.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    module.register_subcommand(subparsers)
    return parser.parse_args([command_name])
