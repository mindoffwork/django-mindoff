import subprocess
import sys
from pathlib import Path
import re

apps_folder = Path("apps")
models_file = "models.py"


# -------------------
# Main Registration
# -------------------
def register_subcommand(subparsers):
    """Register the interactive `create` manager command and handler."""
    def run(args):
        while True:
            command = _choose_create_command()
            if command in {"createmodel", "createapi", "create_model_field"}:
                if _run_repeating_create_flow(command):
                    return
                continue

            remaining_args = _create_app_flow() if command == "createapp" else []
            _run_create_command(command, remaining_args)
            return

    parser = subparsers.add_parser(
        "create", help="Guided interactive creator for apps, models, and APIs"
    )
    parser.set_defaults(handler=run)


# -------------------
# 1. Create App
# -------------------
def _create_app_flow():
    apps = input(
        "\nApp name(s) (space-separated snake_case, e.g. app_one app_two): "
    ).strip()
    return apps.split() if apps else []


# -------------------
# 2. Create Model
# -------------------
def _create_model_flow(local_apps, selected_app=None):
    if not local_apps:
        print("No valid apps found in 'apps' directory. Exiting.")
        return [], None

    app_name = _choose_app(local_apps, selected_app)
    if not app_name:
        return [], None

    while True:
        model_input = input(
            "\nModel name(s) (PascalCase, space-separated, e.g. ProductItem OrderLine): "
        ).strip()
        model_names = model_input.split()
        if model_names and all(
            re.match(r"^[A-Z][a-zA-Z0-9]*$", m) for m in model_names
        ):
            break
        print("Invalid model name(s). Each must be PascalCase (e.g. ProductItem).")

    return [f"{app_name}/{model_name}" for model_name in model_names], app_name


# -------------------
# 3. Create Foreign Key Field
# -------------------
def _create_model_field_flow(local_apps, selected_app=None):
    print("\n# ------- Mindoff > Create > Foreign Key Field ------- #")
    models, model, app_name = _choose_a_existing_model(local_apps, selected_app)
    if not model:
        return [], None

    # Determine existing fields in chosen model for duplicate checking
    app, model_class = model.split("/")
    model_file = apps_folder / app / models_file
    model_text = model_file.read_text() if model_file.exists() else ""

    field_name = _resolve_foreign_key_field_name(model_text, model_class)

    args = [model, field_name]
    args = __create_foreign_key_field_flow(args, models)
    return args, app_name


def __create_foreign_key_field_flow(args, models):
    while True:
        parent = _choose_from_list("Select Parent Model:", models)
        if parent:
            break
        print("Invalid parent model. Please try again.")
    args += ["--to", parent]
    return args


# -------------------
# 4. Create API
# -------------------
def _create_api_flow(local_apps, selected_app=None):
    if not local_apps:
        print("No valid apps found in 'apps' directory. Exiting.")
        return [], None

    app_name = _choose_app(local_apps, selected_app)
    if not app_name:
        return [], None

    while True:
        api_name = input("Enter API name (snake_case, e.g. user_profile): ").strip()
        if re.match(r"^[a-z][a-z0-9_]*$", api_name):
            break
        print("Invalid API name. Must be snake_case.")

    args = [f"{app_name}/{api_name}"]

    urls = input(
        "Enter URL(s) for this API, separated by spaces "
        "(e.g., user_profile user/<int:id>/detail).\n"
        "Leave blank to auto create url: "
    ).strip()

    if urls:
        args += ["--url"] + urls.split()

    return args, app_name


# -------------------
# Helper Functions
# -------------------
def _choose_create_command():
    options = {
        "1": ("app", "createapp"),
        "2": ("api", "createapi"),
        "3": ("model", "createmodel"),
        "4": ("foreign-key", "create_model_field"),
    }
    name_to_command = dict(options.values())

    while True:
        print("\n# ------- Mindoff > Create ------- #")
        print("What would you like to create?")
        for num, (name, _) in options.items():
            print(f"{num}. {name}")

        choice = input("Enter choice of number: ").strip()
        if choice in options:
            return options[choice][1]
        if choice in name_to_command:
            return name_to_command[choice]
        print("Invalid choice. Please try again.")


def _run_repeating_create_flow(command):
    local_apps = _get_local_apps()
    selected_app = None

    while True:
        args, selected_app = _build_create_args(command, local_apps, selected_app)
        if not args:
            return False

        _run_create_command(command, args)
        action = _choose_post_create_action()

        if action == "same_app":
            continue
        if action == "different_app":
            local_apps = _get_local_apps()
            selected_app = None
            continue
        if action == "main_menu":
            return False
        return True


def _build_create_args(command, local_apps, selected_app):
    if command == "createmodel":
        return _create_model_flow(local_apps, selected_app)
    if command == "createapi":
        return _create_api_flow(local_apps, selected_app)
    return _create_model_field_flow(local_apps, selected_app)


def _run_create_command(command, args):
    if not args:
        return

    if command == "createmodel":
        for model_path in args:
            subprocess.run([sys.executable, "mindoff.py", command, model_path])
        return

    subprocess.run([sys.executable, "mindoff.py", command] + args)


def _choose_post_create_action():
    options = {
        "1": "same_app",
        "2": "different_app",
        "3": "main_menu",
        "4": "exit",
    }

    print("\nWhat would you like to do next?")
    print("1. Create another in the same app (default)")
    print("2. Choose a different app")
    print("3. Go to main menu")
    print("4. Exit")

    while True:
        choice = input("Enter choice of number (default: 1): ").strip() or "1"
        if choice in options:
            return options[choice]
        print("Invalid choice. Please try again.")


def _choose_app(local_apps, selected_app=None):
    if selected_app:
        return selected_app

    while True:
        app_name = _choose_from_list("Select an app:", local_apps)
        if app_name:
            return app_name
        print("Invalid app selection. Please try again.")


def _get_local_apps():
    return [
        d.name
        for d in apps_folder.iterdir()
        if d.is_dir()
        and (apps_folder / d.name / "__init__.py").exists()
        and (
            (apps_folder / d.name / "apps.py").exists()
            or (apps_folder / d.name / models_file).exists()
        )
    ]


def _choose_from_list(prompt, items, bracket_suffix=""):
    print(f"\n{prompt}")
    for i, item in enumerate(items, 1):
        print(f"{i}. {item}")
    choice = input(f"Enter choice of 'number' {bracket_suffix}: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(items):
        return items[int(choice) - 1]
    if choice in items:
        return choice
    return None


def _choose_a_existing_model(local_apps, selected_app=None):
    models = []

    for app in local_apps:
        if selected_app and app != selected_app:
            continue

        file_path = apps_folder / app / models_file
        if file_path.exists():
            lines = file_path.read_text().splitlines()
            models.extend(
                f"{app}/{line.split('class ')[1].split('(')[0]}"
                for line in lines
                if line.strip().startswith("class ") and "(" in line
            )

    if not models:
        print("No existing models found. Exiting.")
        return [], None, None

    while True:
        model = _choose_from_list("Select a Model:", models)
        if model:
            app_name = model.split("/")[0]
            return models, model, app_name
        print("Invalid model selection. Please try again.")


def _resolve_foreign_key_field_name(model_text, model_class):
    field_name = input(
        "\nField name (snake_case, leave blank to auto-generate): "
    ).strip()
    if not field_name:
        return field_name

    while True:
        if not re.match(r"^[a-z][a-z0-9_]*$", field_name) or field_name.endswith("_"):
            print("Invalid field name. Must be snake_case (e.g. to_account).")
            field_name = input("Field name: ").strip()
            continue

        normalised = _normalise_foreign_key_field_name(field_name)
        if re.search(rf"\b{normalised}\s*=", model_text):
            print(
                f"Field '{normalised}' already exists in {model_class}. You must provide a different name."
            )
            field_name = input("Field name: ").strip()
            continue
        return field_name


def _normalise_foreign_key_field_name(field_name):
    return field_name if field_name.endswith("_ref") else f"{field_name}_ref"
