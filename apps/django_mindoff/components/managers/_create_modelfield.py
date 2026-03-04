import re
from pathlib import Path

from ..helper_kit import mo_helper_kit


# ======== CONSTANTS ========
MODEL_FILE_NAME = "models.py"

FIELD_INSERT_MARKER = (
    "# Add model fields above this line -- "
    "(MANAGED BY MINDOFF. DO NOT TOUCH THIS LINE)"
)


# ======== CLASSES ========
class DjangoModelFieldCreator:
    """
    Creates a ForeignKey field inside an existing Django model.
    """

    def __init__(self, model_path: str, field_name: str, to: str | None = None):
        self.model_path = model_path
        self.field_name = field_name
        self.to = to

    # -------------------------
    # Parsing & Validation
    # -------------------------
    def _parse_model_path(self):
        try:
            app, model_raw = self.model_path.split("/")
        except ValueError:
            raise ValueError("Model path must be in format <app_name>/<ModelName>")

        self.app_slug = app.lower()
        self.model_name = self._format_model_name(model_raw)

        self.model_file = Path.cwd() / "apps" / self.app_slug / MODEL_FILE_NAME

        if not self.model_file.exists():
            raise FileNotFoundError(f"models.py not found for app '{self.app_slug}'")

        model_text = self.model_file.read_text()
        if f"class {self.model_name}(" not in model_text:
            raise ValueError(
                f"Model '{self.model_name}' not found in {self.model_file}"
            )

        if FIELD_INSERT_MARKER not in model_text:
            raise ValueError(
                f"Field insert marker not found in {self.model_name}. "
                "Refusing to modify file."
            )

    def _format_model_name(self, name: str) -> str:
        pascal = re.sub(
            r"(?:^|_)([a-z])",
            lambda m: m.group(1).upper(),
            name,
        )
        return pascal if pascal.endswith("Model") else f"{pascal}Model"

    def _get_model_block(self, lines: list[str]) -> tuple[int, int]:
        class_pattern = f"class {self.model_name}("
        start = None

        for i, line in enumerate(lines):
            if line.startswith(class_pattern):
                start = i
                break

        if start is None:
            raise ValueError(f"Model '{self.model_name}' not found")

        class_indent = len(lines[start]) - len(lines[start].lstrip())

        for i in range(start + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= class_indent:
                return start, i

        return start, len(lines)

    def _validate_field_name(self):
        if not self.field_name:
            return
        if self.field_name.endswith("_"):
            raise ValueError(
                f"Foreign key field name '{self.field_name}' cannot end with '_'"
            )
        if not re.match(r"^[a-z][a-z0-9_]*$", self.field_name):
            raise ValueError(f"Invalid field name '{self.field_name}'")
        if not self.field_name.endswith("_ref"):
            self.field_name = f"{self.field_name}_ref"

    def _validate_foreign_key(self):
        if not self.to:
            raise ValueError("--to is required for foreign_key")

        try:
            parent_app, parent_model_raw = self.to.split("/")
        except ValueError:
            raise ValueError("--to must be in format <app_name>/<ModelName>")

        parent_app = parent_app.lower()
        parent_model = self._format_model_name(parent_model_raw)

        parent_model_file = Path.cwd() / "apps" / parent_app / MODEL_FILE_NAME

        if not parent_model_file.exists():
            raise FileNotFoundError(f"Parent app '{parent_app}' not found")

        if f"class {parent_model}(" not in parent_model_file.read_text():
            raise ValueError(f"Parent model '{parent_model}' not found")

        self.parent_app = parent_app
        self.parent_model = parent_model

        if not self.field_name:
            base = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_model).lower()
            base = base.replace("_model", "")
            self.field_name = f"{base}_ref"
            print(f"[OK] Auto-generated field name: '{self.field_name}'")

        lines = self.model_file.read_text().splitlines()
        start, end = self._get_model_block(lines)
        model_block = "\n".join(lines[start:end])
        if re.search(rf"\b{self.field_name}\s*=", model_block):
            raise ValueError(
                f"Field '{self.field_name}' already exists in {self.model_name}"
            )

    # -------------------------
    # Field Builder
    # -------------------------
    def _build_foreign_key_field(self) -> str:
        related_name = (
            f"{self.app_slug}_"
            f"{self.model_name.replace('Model', '').lower()}_"
            f"{self.field_name}_rev"
        )

        target_model = f'"{self.parent_app}.{self.parent_model}"'

        options = [
            "on_delete=models.CASCADE",
            f"related_name='{related_name}'",
            f"db_column='{self.field_name}_id'",
        ]

        options_block = ",\n        ".join(options)

        return (
            f"{self.field_name} = models.ForeignKey(\n"
            f"        {target_model},\n"
            f"        {options_block}\n"
            f"    )"
        )

    # -------------------------
    # File Writer
    # -------------------------
    def _append_field_to_model(self, field_code: str):
        lines = self.model_file.read_text().splitlines()
        start, end = self._get_model_block(lines)

        marker_index = None
        marker_indent = ""

        for i in range(start, end):
            if FIELD_INSERT_MARKER in lines[i]:
                marker_index = i
                marker_indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                break

        if marker_index is None:
            raise ValueError(f"Field insert marker not found inside {self.model_name}")

        if marker_index > start and lines[marker_index - 1].strip():
            lines.insert(marker_index, "")

        lines.insert(
            marker_index,
            f"{marker_indent}{field_code.replace('\n', f'\n{marker_indent}')}",
        )

        self.model_file.write_text("\n".join(lines))
        print(
            f"[OK] Added ForeignKey '{self.field_name}' to {self.model_name}. Any additional parameters need to be added in respective models.py"
        )

    # -------------------------
    # Runner
    # -------------------------
    @mo_helper_kit.file_guardian
    def run(self):
        self._parse_model_path()
        self._validate_field_name()
        self._validate_foreign_key()

        field_code = self._build_foreign_key_field()
        self._append_field_to_model(field_code)


# ======== FUNCTIONS ========
def register_subcommand(subparsers):
    def _create_model_field(args):
        DjangoModelFieldCreator(
            model_path=args.model_path,
            field_name=args.field_name,
            to=args.to,
        ).run()

    parser = subparsers.add_parser(
        "create_model_field",
        help="Create a ForeignKey field inside a Django model",
    )
    parser.add_argument("model_path", help="<app_name>/<ModelName>")
    parser.add_argument(
        "field_name", help="Field name (snake_case, leave blank to auto-generate)"
    )
    parser.add_argument("--to", help="Target model for ForeignKey (<app>/<Model>)")
    parser.set_defaults(handler=_create_model_field)
