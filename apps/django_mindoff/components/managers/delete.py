import os
import sys
import subprocess


# -------------------
# Helper Function
# -------------------
def _delete_apps_via_subprocess():
    """Handle interactive app deletion and call subprocess."""
    app_names = _get_valid_app_names()
    if app_names is None:
        print("No 'apps' directory found. Exiting.")
        return
    if not app_names:
        print("No valid apps found in 'apps' directory. Exiting.")
        return

    chosen_apps = _choose_apps_to_delete(app_names)

    subprocess.run([sys.executable, "mindoff.py", "deleteapp"] + chosen_apps)


# -------------------
# Main Registration
# -------------------
def register_subcommand(subparsers):
    def run(args):
        options = {"1": ("app", "deleteapp")}
        command = None
        while True:
            print("\n# ------- Mindoff > Delete ------- #")
            print("What would you like to delete ?")
            for num, (label, _) in options.items():
                print(f"{num}. {label}")
            choice = input("\nEnter choice of number: ").strip().lower()
            command = _resolve_delete_command(choice, options)
            if command:
                break
            print("Invalid choice. Please try again.")

        if command == "deleteapp":
            _delete_apps_via_subprocess()

    parser = subparsers.add_parser("delete", help="Guided interactive removal")
    parser.set_defaults(handler=run)


def _get_valid_app_names():
    apps_dir = os.path.join(os.getcwd(), "apps")
    if not os.path.exists(apps_dir):
        return None
    return [
        d
        for d in os.listdir(apps_dir)
        if os.path.isdir(os.path.join(apps_dir, d))
        and os.path.exists(os.path.join(apps_dir, d, "__init__.py"))
    ]


def _choose_apps_to_delete(app_names):
    while True:
        print("\nSelect app(s) to delete:")
        for idx, app in enumerate(app_names, start=1):
            print(f"{idx}. {app}")

        raw_choices = (
            input("\nEnter choice of number(s) (space separated): ").strip().split()
        )
        chosen_apps = _parse_app_choices(raw_choices, app_names)
        if chosen_apps:
            return chosen_apps
        print("No valid apps selected. Please try again.")


def _parse_app_choices(raw_choices, app_names):
    chosen_apps = []
    for choice in raw_choices:
        if choice.isdigit() and 1 <= int(choice) <= len(app_names):
            chosen_apps.append(app_names[int(choice) - 1])
        elif choice in app_names:
            chosen_apps.append(choice)
        else:
            print(f"Invalid choice ignored: {choice}")
    return list(dict.fromkeys(chosen_apps))


def _resolve_delete_command(choice, options):
    if choice in options:
        return options[choice][1]
    return next((cmd for label, cmd in options.values() if label == choice), None)
