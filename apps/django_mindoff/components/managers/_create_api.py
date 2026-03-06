import re
from pathlib import Path

from ..helper_kit import mo_helper_kit

TEMPLATE_PATH = Path(__file__).parent / "resources" / "api_class.txt"
TEST_TEMPLATE_PATH = Path(__file__).parent / "resources" / "test_api_class.txt"
VERSION_ROUTER_TEMPLATE_PATH = (
    Path(__file__).parent / "resources" / "api_router_class.txt"
)
TEST_ROUTER_TEMPLATE_PATH = (
    Path(__file__).parent / "resources" / "test_api_router_class.txt"
)
API_CLASS_TEMPLATE_NAME = "{{API_HUMAN_NAME}}"


class DjangoApiCreator:
    def __init__(
        self, api_path: str, url_paths: list[str] = None, base_path: Path = None
    ):
        self.api_path = api_path
        self.base_path = Path(base_path) if base_path else Path.cwd() / "apps"
        self.url_paths = url_paths or []
        self.original_app_name = None
        self.raw_api = None
        self.normalized_app_name = None
        self.app = None
        self.api_function_name = None
        self.api_class_name = None
        self.api_router_name = None
        self.api_human_name = None

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------

    def _normalize_app_name(self, dotted_path: str) -> str:
        if self.base_path != Path.cwd() / "apps":
            return dotted_path.lower()
        if not dotted_path.startswith("apps."):
            dotted_path = f"apps.{dotted_path}"
        app_names = dotted_path.split(".")
        if len(app_names) != 2:
            raise ValueError(f"Invalid App Name or Path: '{dotted_path}'")
        normalized = [app_names[0]] + [p.lower() for p in app_names[1:]]
        if any(not p for p in normalized):
            raise ValueError(f"Invalid path '{dotted_path}'")
        return ".".join(normalized)

    def _normalize_api_name(self, raw: str):
        if not re.match(r"^[a-z_]+$", raw):
            raise ValueError(
                f"Invalid API name: '{raw}'. Only lowercase letters and underscores allowed."
            )
        snake = raw
        pascal = re.sub(r"(?:^|_)([a-z])", lambda m: m.group(1).upper(), snake)
        class_name = pascal + "V1APIView"
        return snake, class_name

    def _normalize_url(self, url: str) -> str:
        url = url.strip()
        if url.startswith("/"):
            url = url[1:]
        if not url.endswith("/"):
            url += "/"
        if re.search(r"<[^>:]+>", url):
            raise ValueError(
                f"Invalid URL pattern '{url}': use format like <int:id>, <slug:name>"
            )
        if not re.match(r"^[\w\-/<>:]+/$", url):
            raise ValueError(f"Invalid characters in URL pattern: '{url}'")
        return url

    # ------------------------------------------------------------------
    # Input parsing
    # ------------------------------------------------------------------

    def _parse_input(self):
        try:
            self.original_app_name, self.raw_api = self.api_path.split("/")
        except ValueError:
            raise ValueError("API path must be in format <app_name>/<api_name>")

        self.normalized_app_name = self._normalize_app_name(self.original_app_name)
        self.app = self.normalized_app_name

        _, self.api_class_name = self._normalize_api_name(self.raw_api)
        self.api_function_name = self.raw_api

        words = self.raw_api.split("_")
        self.api_human_name = " ".join(w.capitalize() for w in words)

        self.api_router_name = f"{self.raw_api}_router"
        self.api_router_class_name = "".join(w.capitalize() for w in words) + "Router"

    # ------------------------------------------------------------------
    # Step 1 – write versioned class into apps/<app>/api/<api_name>.py
    # ------------------------------------------------------------------

    def _write_versioned_api_file(self):
        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"API template not found at {TEMPLATE_PATH}")

        api_dir = self.base_path / self.original_app_name / "apis"
        api_file = api_dir / f"{self.raw_api}.py"

        content = TEMPLATE_PATH.read_text()

        replaced = re.sub(
            r"class\s+\w+\s*\(",
            f"class {self.api_class_name}(",
            content,
        )
        if f"class {self.api_class_name}(" not in replaced:
            raise ValueError("Could not replace class name in template.")

        replaced = replaced.replace(API_CLASS_TEMPLATE_NAME, self.api_human_name)
        if API_CLASS_TEMPLATE_NAME in replaced:
            raise ValueError("Template is missing API_HUMAN_NAME replacement.")

        api_url_name = f"{self.original_app_name}__{self.api_function_name}"
        replaced = replaced.replace("{{API_URL_NAME}}", api_url_name)

        import_lines, code_lines = self._extract_imports_and_code(replaced)

        if api_file.exists():
            original = api_file.read_text()
            if f"class {self.api_class_name}(" in original:
                raise FileExistsError(
                    f"API '{self.raw_api}' (version 1) already exists in "
                    f"'{api_file}'. Use the upgrade command to add a new version."
                )
            existing_lines = set(original.splitlines())
            missing_imports = [l for l in import_lines if l not in existing_lines]
            final_lines = original.rstrip().splitlines()
            insert_at = self._find_import_insert_point(final_lines)
            new_content = (
                final_lines[:insert_at]
                + missing_imports
                + final_lines[insert_at:]
                + ["", *code_lines, ""]
            )
            api_file.write_text("\n".join(new_content))
        else:
            api_dir.mkdir(parents=True, exist_ok=True)
            init_file = api_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text("")
            full_content = "\n".join(import_lines + ["", *code_lines, ""])
            api_file.write_text(full_content)

    # ------------------------------------------------------------------
    # Step 2 – write / update the version-router function in views.py
    # ------------------------------------------------------------------

    def _write_version_router_to_views(self):
        if not VERSION_ROUTER_TEMPLATE_PATH.exists():
            raise FileNotFoundError(
                f"Version-router template not found at {VERSION_ROUTER_TEMPLATE_PATH}"
            )

        app_dir = self.base_path / self.original_app_name
        view_path = app_dir / "views.py"

        v1_import = f"from .apis.{self.raw_api} import {self.api_class_name}"

        router_template = VERSION_ROUTER_TEMPLATE_PATH.read_text()
        router_code = router_template.replace(
            "SampleRouterClassName", self.api_router_class_name
        )
        router_code = router_code.replace(
            "sample_router_function_name", self.api_router_name
        )
        version_map_entry = f"1: {self.api_class_name},"
        router_code = router_code.replace("mo_api_kit.__str__", version_map_entry)
        router_code = router_code.replace(API_CLASS_TEMPLATE_NAME, self.api_human_name)

        router_import_lines, router_code_lines = self._extract_imports_and_code(
            router_code
        )

        if view_path.exists():
            original = view_path.read_text()
            if f"{self.api_router_name} = {self.api_router_class_name}()" in original:
                raise FileExistsError(
                    f"Version router '{self.api_router_name}' already exists in views.py."
                )
            existing_lines = set(original.splitlines())
            all_new_imports = [v1_import] + router_import_lines
            missing_imports = [l for l in all_new_imports if l not in existing_lines]
            final_lines = original.rstrip().splitlines()
            insert_at = self._find_import_insert_point(final_lines)
            new_content = (
                final_lines[:insert_at]
                + missing_imports
                + final_lines[insert_at:]
                + ["", *router_code_lines, ""]
            )
            view_path.write_text("\n".join(new_content))
        else:
            view_path.parent.mkdir(parents=True, exist_ok=True)
            all_imports = [v1_import] + router_import_lines
            full_content = "\n".join(all_imports + ["", *router_code_lines, ""])
            view_path.write_text(full_content)

    # ------------------------------------------------------------------
    # Step 3a – write API class test into tests/test_apis/<api_name>.py
    # ------------------------------------------------------------------

    def _copy_test_template_and_replace(self):
        if not TEST_TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"Test template not found at {TEST_TEMPLATE_PATH}")

        test_apis_dir = self.base_path / self.original_app_name / "tests" / "test_apis"
        test_file = test_apis_dir / f"test_{self.raw_api}.py"

        content = TEST_TEMPLATE_PATH.read_text()
        replaced = re.sub(
            r"(^\s*class\s+)\w+(\s*(?:\([^)]*\))?\s*:)",
            rf"\1Test{self.api_class_name}\2",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if f"class Test{self.api_class_name}" not in replaced:
            raise ValueError("Could not replace test class name in template.")

        api_url_name = f"{self.original_app_name}__{self.api_function_name}"
        replaced = replaced.replace("{{API_URL_NAME}}", api_url_name)

        import_lines, code_lines = self._extract_imports_and_code(replaced)

        if test_file.exists():
            original = test_file.read_text()
            if f"class Test{self.api_class_name}(" in original:
                raise FileExistsError(
                    f"Test for API '{self.raw_api}' (version 1) already exists in "
                    f"'{test_file}'. Use the upgrade command to add a new version."
                )
            existing_lines = set(original.splitlines())
            missing_imports = [l for l in import_lines if l not in existing_lines]
            final_lines = original.rstrip().splitlines()
            insert_at = self._find_import_insert_point(final_lines)
            new_content = (
                final_lines[:insert_at]
                + missing_imports
                + final_lines[insert_at:]
                + ["", *code_lines, ""]
            )
            test_file.write_text("\n".join(new_content))
        else:
            test_apis_dir.mkdir(parents=True, exist_ok=True)
            init_file = test_apis_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text("")
            full_content = "\n".join(import_lines + ["", *code_lines, ""])
            test_file.write_text(full_content)

    # ------------------------------------------------------------------
    # Step 3b – write router test into tests/test_views.py
    # ------------------------------------------------------------------

    def _write_router_test_to_test_views(self):
        if not TEST_ROUTER_TEMPLATE_PATH.exists():
            raise FileNotFoundError(
                f"Router test template not found at {TEST_ROUTER_TEMPLATE_PATH}"
            )

        tests_dir = self.base_path / self.original_app_name / "tests"
        test_views_path = tests_dir / "test_views.py"

        content = TEST_ROUTER_TEMPLATE_PATH.read_text()

        words = self.api_router_name.split("_")
        router_test_class = "Test" + "".join(w.capitalize() for w in words)

        replaced = content.replace(
            "apps.sample_app_name.views",
            f"apps.{self.original_app_name}.views",
        )
        replaced = replaced.replace("sample_app_name", self.original_app_name)
        replaced = replaced.replace("sample_router_function_name", self.api_router_name)
        replaced = replaced.replace("Sample Api Human Name", self.api_human_name)
        replaced = re.sub(
            r"class\s+SampleRouterTestClassName\s*\(",
            f"class {router_test_class}(",
            replaced,
        )

        if f"class {router_test_class}(" not in replaced:
            raise ValueError("Could not replace router test class name in template.")

        import_lines, code_lines = self._extract_imports_and_code(replaced)

        if test_views_path.exists():
            original = test_views_path.read_text()
            if f"class {router_test_class}(" in original:
                raise FileExistsError(
                    f"Router test '{router_test_class}' already exists in test_views.py."
                )
            existing_lines = set(original.splitlines())
            missing_imports = [l for l in import_lines if l not in existing_lines]
            final_lines = original.rstrip().splitlines()
            insert_at = self._find_import_insert_point(final_lines)
            new_content = (
                final_lines[:insert_at]
                + missing_imports
                + final_lines[insert_at:]
                + ["", *code_lines, ""]
            )
            test_views_path.write_text("\n".join(new_content))
        else:
            tests_dir.mkdir(parents=True, exist_ok=True)
            full_content = "\n".join(import_lines + ["", *code_lines, ""])
            test_views_path.write_text(full_content)

    # ------------------------------------------------------------------
    # Step 4 – register URL pointing to the router function
    # ------------------------------------------------------------------

    def _update_urls(self):
        urls_path = self.base_path / self.original_app_name / "urls.py"
        if not urls_path.exists():
            raise FileNotFoundError(f"urls.py not found at {urls_path}")

        text = urls_path.read_text()
        existing_patterns = set(re.findall(r"path\(\s*['\"](.+?)['\"]", text))
        existing_names = set(re.findall(r"name=['\"](.+?)['\"]", text))
        existing_names_lower = {n.lower() for n in existing_names}

        if not self.url_paths:
            default_url = f"{self.api_function_name}/"
            self.url_paths = [default_url]

        pattern = re.compile(r"(urlpatterns\s*=\s*\[.*?)(\])", re.DOTALL)
        match = pattern.search(text)
        if not match:
            raise ValueError("Could not find urlpatterns list in urls.py")

        insert_lines = []
        for url in self.url_paths:
            norm_url = self._normalize_url(url)
            if norm_url in existing_patterns:
                raise FileExistsError(
                    f"URL pattern '{norm_url}' already exists in urls.py"
                )
            route_name = self._generate_route_name(existing_names, existing_names_lower)
            insert_lines.append(
                f"    path('{norm_url}', csrf_exempt(views.{self.api_router_name}), name='{route_name}'),"
            )

        new_text = pattern.sub(r"\1" + "\n".join(insert_lines) + r"\n\2", text)
        urls_path.write_text(new_text)

    def _generate_route_name(
        self, existing_names: set[str], existing_names_lower: set[str]
    ) -> str:
        route_name = f"{self.original_app_name}__{self.api_function_name}"
        if route_name in existing_names:
            raise FileExistsError(f"URL name '{route_name}' already exists in urls.py")
        if route_name.lower() in existing_names_lower:
            print(
                f"[WARNING] Route name '{route_name}' may collide with an existing "
                f"name if case is ignored."
            )
        return route_name

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _extract_imports_and_code(self, content: str):
        import_lines = []
        code_lines = []
        for line in content.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                import_lines.append(stripped)
            else:
                code_lines.append(line)
        return import_lines, code_lines

    def _find_import_insert_point(self, lines: list[str]) -> int:
        """Return the index of the first non-import, non-blank line."""
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not (
                stripped.startswith("import") or stripped.startswith("from")
            ):
                return i
        return 0

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @mo_helper_kit.file_guardian
    def run(self):
        self._parse_input()
        self._write_versioned_api_file()
        self._write_version_router_to_views()
        self._copy_test_template_and_replace()
        self._write_router_test_to_test_views()
        self._update_urls()


# ======== CLI HOOK ========
def register_subcommand(subparsers):
    def _create_api(args):
        DjangoApiCreator(api_path=args.api_path, url_paths=args.url).run()

    parser = subparsers.add_parser(
        "createapi", help="Create versioned Django API view class and route"
    )
    parser.add_argument("api_path", help="API path in format <app_name>/<api_name>")
    parser.add_argument(
        "--url",
        action="extend",
        nargs="+",
        help=(
            "One or more URL patterns. "
            "Defaults to '<api_name>/' if omitted. "
            "The version kwarg and app prefix are handled by the root urls.py. "
            "Example: 'create/' or 'create/<int:pk>/'"
        ),
    )
    parser.set_defaults(handler=_create_api)
