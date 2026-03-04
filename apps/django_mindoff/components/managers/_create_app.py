import os
import subprocess
from pathlib import Path

from ..helper_kit import mo_helper_kit


# ======== CLASSES =======
# Add Classes here
class DjangoAppCreator:
    def __init__(self, dotted_path: str, *, isolated: bool = False):
        self.isolated = isolated
        if not self.isolated and not dotted_path.startswith("apps."):
            dotted_path = f"apps.{dotted_path}"
        self.original_path = dotted_path

        self.dotted_path = self._normalize_path(dotted_path)
        self.project_root = Path.cwd()
        self.settings_path = os.path.join(self.project_root, "config", "settings.py")
        self.urls_path = os.path.join(self.project_root, "config", "urls.py")
        self.app_name = self.dotted_path.split(".")[-1]
        self.app_dir = os.path.join(
            self.project_root, self.dotted_path.replace(".", "/")
        )

    def _normalize_path(self, dotted_path: str) -> str:
        app_names = dotted_path.split(".")
        if not self.isolated and len(app_names) != 2:
            raise ValueError(f"Invalid App Name '{dotted_path}'")
        normalized = [app_names[0]] + [p.lower() for p in app_names[1:]]
        if any(not p for p in normalized):
            raise ValueError(
                f"Invalid path '{dotted_path}': segments cannot be empty after normalization."
            )
        return ".".join(normalized)

    def _create_directories(self):
        os.makedirs(self.app_dir, exist_ok=True)
        parts = self.dotted_path.replace(".", "/").split("/")
        for i in range(1, len(parts)):
            init_dir = os.path.join(self.project_root, *parts[:i])
            open(os.path.join(init_dir, "__init__.py"), "a").close()

    def _run_startapp(self):
        print(f"[ACTION] Creating App at: {self.app_dir}.")
        subprocess.run(
            ["python", "manage.py", "startapp", self.app_name, self.app_dir], check=True
        )

    def _overwrite_apps_py(self):
        print("[ACTION] Updating apps.py.")
        apps_path = os.path.join(self.app_dir, "apps.py")
        with open(apps_path, "w") as f:
            f.write(
                f"from django.apps import AppConfig\n\n"
                f"class {self.app_name.capitalize()}Config(AppConfig):\n"
                f"    default_auto_field = 'django.db.models.BigAutoField'\n"
                f"    name = '{self.dotted_path}'\n"
            )

    def _create_urls_py(self):
        print("[ACTION] Creating urls.py.")
        urls_path = os.path.join(self.app_dir, "urls.py")
        with open(urls_path, "w") as f:
            f.write(
                "from django.urls import path\n"
                "from django.views.decorators.csrf import csrf_exempt\n"
                "from . import views\n\n"
                "urlpatterns = [\n"
                "# Add Url Patterns here\n"
                "]\n"
            )

    def _create_serializers_py(self):
        print("[ACTION] Creating serializers.py.")
        serializers_path = os.path.join(self.app_dir, "serializers.py")
        with open(serializers_path, "w") as f:
            f.write("from rest_framework import serializers\nfrom . import models\n")

    def _patch_models_py(self):
        print("[ACTION] Patching models.py.")
        path = os.path.join(self.app_dir, "models.py")
        if not os.path.exists(path):
            return
        line = "import uuid\nfrom django_mindoff import models as mindoff_models\n"
        with open(path, "r+") as f:
            lines = f.readlines()
            if line in lines:
                return
            for i, l in enumerate(lines):
                if not l.strip().startswith(("import", "from ")):
                    lines.insert(i, line)
                    break
            else:
                lines.append(line)
            f.seek(0)
            f.writelines(lines)
            f.truncate()

    def _setup_tests_folder(self):
        print(
            "[ACTION] Creating 'tests', 'components' and 'apis' folder with __init__.py."
        )
        tests_py = os.path.join(self.app_dir, "tests.py")
        admin_py = os.path.join(self.app_dir, "admin.py")
        if os.path.exists(tests_py):
            os.remove(tests_py)
        if os.path.exists(admin_py):
            os.remove(admin_py)
        tests_folder = os.path.join(self.app_dir, "tests")
        os.makedirs(tests_folder, exist_ok=True)
        open(os.path.join(tests_folder, "__init__.py"), "w").close()
        components_folder = os.path.join(self.app_dir, "components")
        os.makedirs(components_folder, exist_ok=True)
        open(os.path.join(components_folder, "__init__.py"), "w").close()
        apis_folder = os.path.join(self.app_dir, "apis")
        os.makedirs(apis_folder, exist_ok=True)
        open(os.path.join(apis_folder, "__init__.py"), "w").close()

    def _update_settings(self):
        with open(self.settings_path, "r+") as f:
            content = f.read()
            if f"'{self.dotted_path}'" in content:
                return
            installed_apps_str = "INSTALLED_APPS = ["
            if installed_apps_str in content:
                print("[ACTION] Updating settings.py.")
                start = content.index(installed_apps_str) + len(installed_apps_str)
                end = content.index("\n]", start)
                updated = content[start:end].rstrip() + f"\n    '{self.dotted_path}',"
                content = content[:start] + updated + content[end:]
                f.seek(0)
                f.write(content)
                f.truncate()

    def _update_project_urls(self):
        with open(self.urls_path, "r+") as f:
            content = f.read()
            url_prefix = self.original_path.split(".")[-1]
            route = f"path('v<int:version>/{url_prefix}/', include('{self.dotted_path}.urls')),"
            if route not in content:
                print("[ACTION] Linking urls.py.")
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.strip() == "]":
                        lines.insert(i, f"    {route}")
                        break
                content = "\n".join(lines)
                f.seek(0)
                f.write(content)
                f.truncate()

    @mo_helper_kit.file_guardian
    def run(self):
        if os.path.exists(self.app_dir):
            print(
                f"[ACTION] App '{self.dotted_path}' already exists at: {self.app_dir}. Skipping."
            )
            return
        self._create_directories()
        self._run_startapp()
        self._overwrite_apps_py()
        self._create_urls_py()
        self._create_serializers_py()
        if not self.isolated:
            self._patch_models_py()
        self._setup_tests_folder()
        if not self.isolated:
            self._update_settings()
            self._update_project_urls()

        print(f"[OK] App creation complete for: {self.dotted_path}.")


# ======== FUNCTIONS =======
# Add Functions here
# F1. Command Entry Point -- Registers the command into the CLI.
def register_subcommand(subparsers):
    def _create_app(args):
        for app_name in args.app_names:
            DjangoAppCreator(app_name).run()

    parser = subparsers.add_parser(
        "createapp", help="Create a new Django app under the 'apps' folder"
    )
    parser.add_argument(
        "app_names",
        nargs="+",
        help="App Name for the app e.g., 'blog' or stack multiple apps like 'app1 app2'",
    )
    parser.set_defaults(handler=_create_app)
