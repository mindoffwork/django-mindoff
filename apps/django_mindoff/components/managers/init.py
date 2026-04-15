import os
import re
import shutil
import subprocess
import tomllib
from importlib import metadata as importlib_metadata
from pathlib import Path

from ..helper_kit import mo_helper_kit


# ======== CLASSES =======
class DjangoProjectCreator:
    """
    Initialize a new Django-Mindoff project workspace in the current directory.

    This manager orchestrates end-to-end bootstrap tasks including virtual
    environment setup, dependency installation, Django project scaffolding,
    settings and URL wiring, template/resource seeding, and initial Git setup.
    """
    def __init__(self, apps_dir_name="apps", venv_name=".venv"):
        self.project_root = Path.cwd()
        self.apps_dir_name = apps_dir_name
        self.app_dir_path = self.project_root / apps_dir_name
        self.config_dir = self.project_root / "config"
        self.settings_path = self.config_dir / "settings.py"
        self.urls_path = self.config_dir / "urls.py"
        self.venv_name = venv_name
        self.py_cmd = self.project_root / venv_name / "Scripts" / "python"
        self.pip_cmd = self.project_root / venv_name / "Scripts" / "pip"
        self.django_admin_cmd = (
            self.project_root / venv_name / "Scripts" / "django-admin"
        )
        self.optional_packages = []

    @mo_helper_kit.file_guardian
    def run(self):
        """Execute the full guided project initialization workflow."""
        os.chdir(self.project_root)
        print("\n# ------- Mindoff > Init ------- #")
        print("You're about to set up the following in the current directory:")
        print("  • Django-Mindoff project")
        print("  • Virtual environment")
        print("  • Git repository\n")
        confirm = input("Proceed to initialize project? [y/N]: ").strip().lower()
        if confirm != "y":
            print("[ACTION] Aborted project initialization. Exiting.")
            return

        self.optional_packages = self._prompt_optional_dependencies()
        self._create_venv()
        self._install_packages()
        self._initialize_django_project()
        self._update_settings()
        self._create_env_file()
        self._update_urls()
        self._create_extra_folders()
        self._write_supporting_files()
        self._initialize_git()
        print("[OK] Django-Mindoff project initialization complete.")

    def _prompt_optional_dependencies(self):
        optional = []
        # Sample if required -- if input("Install polars? (y/n): ").lower() == "y":
        #     Sample if required -- optional.extend(["polars", "numpy"])
        return optional

    def _create_venv(self):
        print(f"[ACTION] Creating virtual environment in '{self.venv_name}'.")
        if not os.path.exists(self.venv_name):
            subprocess.run(["python", "-m", "venv", self.venv_name], check=True)
        else:
            print("[ACTION] Virtual environment exists, skipping creation.")

    def _install_packages(self):
        print("[ACTION] Installing required packages.")

        base_packages = self._get_base_packages()
        if not base_packages:
            print(
                "[WARN] No dependencies found in pyproject.toml. "
                "Skipping package installation."
            )
            return
        subprocess.run(
            [self.pip_cmd, "install", *base_packages],
            check=True,
        )
        if self.optional_packages:
            print(
                f"[ACTION] Installing optional packages: {', '.join(self.optional_packages)}"
            )
            subprocess.run(
                [self.pip_cmd, "install", *self.optional_packages], check=True
            )
        subprocess.run([self.pip_cmd, "install", "django-mindoff"], check=True)

    def _initialize_django_project(self):
        print("[ACTION] Creating Django project.")
        subprocess.run(
            [self.django_admin_cmd, "startproject", "config", "."], check=True
        )

    def _update_settings(self):
        print("[ACTION] Updating settings.py.")
        content = self.settings_path.read_text()
        lines, secret_key = [], ""
        insert_pos = {}
        lines, secret_key, insert_pos = self._extract_secret_and_debug_info(
            content, lines, secret_key, insert_pos
        )
        if "from decouple import config" not in content:
            for i, line in enumerate(lines):
                if "from pathlib import Path" in line:
                    lines.insert(i + 1, "from decouple import config")
                    break
        if "SECRET_KEY" in insert_pos:
            lines.insert(
                insert_pos["SECRET_KEY"], "SECRET_KEY = config('DJANGO_SECRET_KEY')"
            )
        if "DEBUG" in insert_pos:
            lines.insert(
                insert_pos["DEBUG"], "DEBUG = config('DEBUG', cast=bool, default=True)"
            )
        updated = "\n".join(lines)
        updated = self._ensure_host_and_cors_config(updated)
        updated = self._append_to_list(updated, "INSTALLED_APPS", "rest_framework")
        updated = self._append_to_list(
            updated, "INSTALLED_APPS", "rest_framework.authtoken"
        )
        updated = self._append_to_list(updated, "INSTALLED_APPS", "django_mindoff")
        updated = self._ensure_list_item_before(
            updated, "INSTALLED_APPS", "corsheaders", "rest_framework"
        )
        updated = self._ensure_list_item_first(
            updated,
            "MIDDLEWARE",
            "corsheaders.middleware.CorsMiddleware",
        )
        if "TEMPLATES" in updated:
            if "import os" not in updated:
                updated = updated.replace(
                    "from pathlib import Path",
                    "import os\nfrom pathlib import Path",
                )
            updated = re.sub(
                r'"DIRS"\s*:\s*\[[^\]]*\]',
                '"DIRS": [os.path.join(BASE_DIR, "templates")]',
                updated,
            )
        mindoff_header = "# ===== MINDOFF SPECIFIC SETTINGS OPTIONS ====="
        if mindoff_header not in updated:
            updated += f"""
{mindoff_header}
AUTH_USER_MODEL = "django_mindoff.User"
MINDOFF_LOG_ERRORS_IN_DEBUG = False
MINDOFF_TRACEBACK_DIRS = ["apps", "config"]
REDIS_URL = config("REDIS_URL")
POLARS_VALIDATOR_ERROR_COL = "__error__info"
MINDOFF_USE_VIEW_CACHE = False
MINDOFF_QUEUE_LIST_API_REQUEST_LIMIT = "120/m"
"""

        self.settings_path.write_text(updated)
        self.secret_key = secret_key

        self.settings_path.write_text(updated)
        self.secret_key = secret_key

    def _extract_secret_and_debug_info(self, content, lines, secret_key, insert_pos):
        for idx, line in enumerate(content.splitlines()):
            if line.strip().startswith("SECRET_KEY"):
                secret_key = line.split("=", 1)[1].strip()
                insert_pos["SECRET_KEY"] = idx
                continue
            if line.strip().startswith("DEBUG"):
                insert_pos["DEBUG"] = idx
                continue
            lines.append(line)
        return lines, secret_key, insert_pos

    def _create_env_file(self):
        print("[ACTION] Writing .env file.")
        Path(".env").write_text(
            (
                f"DJANGO_SECRET_KEY={self.secret_key}\n"
                "DEBUG=True\n"
                "# Tighten this for production by using an explicit CORS allowlist.\n"
                "CORS_ALLOW_ALL_ORIGINS=True\n"
                "REDIS_URL=redis://127.0.0.1:6379/0\n"
            )
        )

    def _update_urls(self):
        print("[ACTION] Updating urls.py.")
        content = self.urls_path.read_text()
        content = re.sub(r'^\s*"""(?:.|\n)*?"""', "", content).lstrip()
        if "from django.urls import" in content and "include" not in content:
            content = content.replace(
                "from django.urls import ", "from django.urls import include, "
            )
        elif "from django.urls import" not in content:
            content = "from django.urls import path, include\n" + content
        if "from django_mindoff import urls as mindoff_urls" not in content:
            content = "from django_mindoff import urls as mindoff_urls\n" + content
        if "from django.views.generic.base import TemplateView" not in content:
            content = "from django.views.generic.base import TemplateView\n" + content
        if 'path("mindoff/", include(mindoff_urls))' not in content:
            content = content.replace(
                "urlpatterns = [",
                "urlpatterns = [\n    path('mindoff/', include(mindoff_urls)),",
            )
        if "path('', TemplateView.as_view(" not in content:
            content = content.replace(
                "urlpatterns = [",
                "urlpatterns = [\n    path('', TemplateView.as_view(template_name='index.html')),",
            )
        self.urls_path.write_text(content)

    def _create_extra_folders(self):
        print("[ACTION] Creating apps folder.")
        self.app_dir_path.mkdir(exist_ok=True)
        print("[ACTION] Writing template files.")
        templates_src = Path(__file__).parent / "resources" / "html"
        templates_dst = self.project_root / "templates"
        templates_dst.mkdir(exist_ok=True)
        for html_file in templates_src.glob("*.html"):
            shutil.copy(html_file, templates_dst / html_file.name)

    def _write_supporting_files(self):
        print("[ACTION] Writing mindoff.py CLI runner.")
        source = Path(__file__).parent / "resources" / "mindoff.py"
        target = self.project_root / "mindoff.py"
        shutil.copy(source, target)

        print("[ACTION] Writing AGENTS.md.")
        source = Path(__file__).parent / "resources" / "AGENTS.md"
        target = self.project_root / "AGENTS.md"
        shutil.copy(source, target)

        print("[ACTION] Writing pytest.ini.")
        source = Path(__file__).parent / "resources" / "pytest.ini"
        target = self.project_root / "pytest.ini"
        shutil.copy(source, target)

        print("[ACTION] Writing .gitignore.")
        source = Path(__file__).parent / "resources" / "_gitignore.txt"
        target = self.project_root / ".gitignore"
        shutil.copy(source, target)

        print("[ACTION] Copying responses.csv to config folder.")
        responses_src = Path(__file__).parent / "resources" / "responses.csv"
        responses_dst = self.config_dir / "responses.csv"
        shutil.copy(responses_src, responses_dst)

    def _initialize_git(self):
        print("[ACTION] Setting up Git.")
        Path("README.md").touch()
        subprocess.run(["git", "init"])
        subprocess.run(["git", "add", "."])
        subprocess.run(["git", "commit", "-m", "Initial commit"])

    def _append_to_list(self, text, list_name, value):
        lines = text.splitlines()
        new_lines = []
        inside_list = False
        for line in lines:
            new_lines.append(line)
            if line.strip().startswith(f"{list_name} = ["):
                inside_list = True
            elif inside_list and line.strip().endswith("]"):
                indent = " " * (len(line) - len(line.lstrip()) + 4)
                new_lines.insert(-1, f"{indent}'{value}',")
                inside_list = False
        return "\n".join(new_lines)

    def _ensure_list_item_before(self, text, list_name, value, before_value):
        lines = text.splitlines()
        list_start = None
        list_end = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if list_start is None and stripped.startswith(f"{list_name} = ["):
                list_start = idx
                continue
            if list_start is not None and stripped == "]":
                list_end = idx
                break
        if list_start is None or list_end is None:
            return text

        list_block = lines[list_start + 1:list_end]
        existing_idx = None
        before_idx = None
        for idx, line in enumerate(list_block):
            normalized = line.strip().strip(",").strip("'").strip('"')
            if normalized == value:
                existing_idx = idx
            if normalized == before_value and before_idx is None:
                before_idx = idx

        if existing_idx is not None:
            list_block.pop(existing_idx)
            if before_idx is not None and existing_idx < before_idx:
                before_idx -= 1

        if before_idx is None:
            insert_idx = len(list_block)
        else:
            insert_idx = before_idx

        indent = " " * 4
        if list_block:
            first_entry = list_block[0]
            indent = first_entry[: len(first_entry) - len(first_entry.lstrip())] or indent
        list_block.insert(insert_idx, f"{indent}'{value}',")

        lines[list_start + 1:list_end] = list_block
        return "\n".join(lines)

    def _ensure_list_item_first(self, text, list_name, value):
        lines = text.splitlines()
        list_start = None
        list_end = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if list_start is None and stripped.startswith(f"{list_name} = ["):
                list_start = idx
                continue
            if list_start is not None and stripped == "]":
                list_end = idx
                break
        if list_start is None or list_end is None:
            return text

        list_block = lines[list_start + 1:list_end]
        normalized_items = [
            line.strip().strip(",").strip("'").strip('"') for line in list_block
        ]
        list_block = [
            line
            for line, normalized in zip(list_block, normalized_items)
            if normalized != value
        ]

        indent = " " * 4
        if list_block:
            first_entry = list_block[0]
            indent = first_entry[: len(first_entry) - len(first_entry.lstrip())] or indent
        list_block.insert(0, f"{indent}'{value}',")

        lines[list_start + 1:list_end] = list_block
        return "\n".join(lines)

    def _ensure_host_and_cors_config(self, text):
        if "ALLOWED_HOSTS" not in text:
            return text
        warning_comment = (
            "# SECURITY WARNING: Restrict ALLOWED_HOSTS and CORS in production."
        )
        cors_line = (
            'CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", cast=bool, default=True)'
        )
        if warning_comment not in text:
            text = re.sub(
                r"^ALLOWED_HOSTS\s*=.*$",
                f"{warning_comment}\nALLOWED_HOSTS = []",
                text,
                count=1,
                flags=re.MULTILINE,
            )
        if cors_line not in text:
            text = re.sub(
                r"^(ALLOWED_HOSTS\s*=.*)$",
                r"\1\n" + cors_line,
                text,
                count=1,
                flags=re.MULTILINE,
            )
        return text

    def _get_base_packages(self):
        pyproject_path = self.project_root / "pyproject.toml"
        packages = self._read_pyproject_dependencies(
            pyproject_path, optional_group="internal"
        )
        if packages:
            return packages
        return self._read_installed_dependencies(optional_group="internal")

    def _read_pyproject_dependencies(self, path, optional_group=None):
        if not path.exists():
            return []
        data = tomllib.loads(path.read_text())
        project = data.get("project", {})
        base = project.get("dependencies", []) or []
        optional = []
        if optional_group:
            opt = project.get("optional-dependencies", {})
            optional = opt.get(optional_group, []) or []

        # Preserve order and dedupe
        seen = set()
        combined = []
        for item in list(base) + list(optional):
            if item not in seen:
                combined.append(item)
                seen.add(item)
        return combined

    def _read_installed_dependencies(self, optional_group=None):
        try:
            requires = importlib_metadata.requires("django-mindoff") or []
        except importlib_metadata.PackageNotFoundError:
            return []

        def _matches_optional_group(marker):
            if not optional_group:
                return marker is None or marker.strip() == ""
            if marker is None:
                return True
            return f"extra == '{optional_group}'" in marker or f'extra == "{optional_group}"' in marker

        combined = []
        seen = set()
        for req in requires:
            if ";" in req:
                req_part, marker = req.split(";", 1)
                marker = marker.strip()
            else:
                req_part, marker = req, None
            if not _matches_optional_group(marker):
                continue
            req_part = req_part.strip()
            if req_part and req_part not in seen:
                combined.append(req_part)
                seen.add(req_part)
        return combined


# ======== FUNCTIONS =======
def register_subcommand(subparsers):
    """Register the `init` manager command and handler."""
    def _create_project(args):
        DjangoProjectCreator().run()

    parser = subparsers.add_parser("init", help="Initialize a new Django Project.")
    parser.set_defaults(handler=_create_project)
