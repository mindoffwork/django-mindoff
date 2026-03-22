import os
import shutil
from pathlib import Path

from ..helper_kit import mo_helper_kit


# ======== CLASSES =======
# Add Classes here
class DjangoAppDeleter:
    """
    Delete a Django app and clean project references safely.

    Removes the app directory, clears empty parent namespace folders when possible,
    and updates project `settings.py` and `urls.py` to remove app references.
    """
    def __init__(self, dotted_path: str):
        if not dotted_path.startswith("apps."):
            dotted_path = f"apps.{dotted_path}"
        self.original_path = dotted_path
        self.dotted_path = self._normalize_path(dotted_path)
        self.project_root = Path.cwd()
        self.src_dir = os.path.join(self.project_root, "apps")
        self.settings_path = os.path.join(self.project_root, "config", "settings.py")
        self.urls_path = os.path.join(self.project_root, "config", "urls.py")
        self.app_name = self.dotted_path.split(".")[-1]
        self.app_dir = os.path.join(self.project_root, *self.dotted_path.split("."))

    def _normalize_path(self, dotted_path: str) -> str:
        app_names = dotted_path.split(".")
        if len(app_names) != 2:
            raise ValueError(f"Invalid App Name '{dotted_path}'")
        normalized = [app_names[0]] + [p.lower() for p in app_names[1:]]
        if any(not p for p in normalized):
            raise ValueError(
                f"Invalid path '{dotted_path}': segments cannot be empty after normalization."
            )
        return ".".join(normalized)

    def _confirm_deletion(self) -> bool:
        confirm = (
            input(
                f"Are you sure you want to delete the app '{self.dotted_path}'? (y/N): "
            )
            .strip()
            .lower()
        )
        return confirm == "y"

    def _delete_app_dir(self):
        shutil.rmtree(self.app_dir)
        print(f"[OK] Deleted app directory: {self.app_dir}")

    def _clean_empty_parent_dirs(self):
        parent_dir = os.path.dirname(self.app_dir)
        while parent_dir != self.src_dir and os.path.isdir(parent_dir):
            try:
                entries = os.listdir(parent_dir)
                non_init_entries = [e for e in entries if e != "__init__.py"]
                if not non_init_entries:
                    init_file = os.path.join(parent_dir, "__init__.py")
                    if os.path.exists(init_file):
                        os.remove(init_file)
                    os.rmdir(parent_dir)
                else:
                    break
            except Exception:
                break
            parent_dir = os.path.dirname(parent_dir)

    def _remove_from_settings(self):
        with open(self.settings_path, "r+") as f:
            content = f.read()
            if self.dotted_path in content:
                lines = content.splitlines()
                updated = [line for line in lines if self.dotted_path not in line]
                f.seek(0)
                f.write("\n".join(updated))
                f.truncate()
                print("[OK] App Removed from settings.py")

    def _remove_from_urls(self):
        with open(self.urls_path, "r+") as f:
            content = f.read()
            url_prefix = self.original_path.split(".")[-1]
            route = f"path('v<int:version>/{url_prefix}/', include('{self.dotted_path}.urls')),"
            lines = content.splitlines()
            updated = [line for line in lines if route not in line]
            f.seek(0)
            f.write("\n".join(updated))
            f.truncate()
            print("[OK] App Removed route from urls.py")

    @mo_helper_kit.file_guardian
    def run(self):
        """Execute app deletion after user confirmation."""
        if not os.path.exists(self.app_dir):
            print(f"[ERROR] App directory not found: {self.app_dir}")
            return

        if not self._confirm_deletion():
            print("[ACTION] Deletion cancelled. Exiting.")
            return

        self._delete_app_dir()
        self._clean_empty_parent_dirs()
        self._remove_from_settings()
        self._remove_from_urls()
        print("[OK] App deletion complete:", self.dotted_path)


# ======== FUNCTIONS =======
# Add Functions here
# F1. Command Entry Point -- Registers the command into the CLI.
def register_subcommand(subparsers):
    """Register the `deleteapp` manager command and handler."""
    def _delete_app(args):
        for app_name in args.app_names:
            DjangoAppDeleter(app_name).run()

    parser = subparsers.add_parser("deleteapp", help="Delete django apps.")
    parser.add_argument(
        "app_names",
        nargs="+",
        help="App Name for the app e.g., 'blog' or stack multiple apps like 'app1 app2'",
    )
    parser.set_defaults(handler=_delete_app)
