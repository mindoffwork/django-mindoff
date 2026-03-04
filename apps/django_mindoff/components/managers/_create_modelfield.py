import re
from pathlib import Path

from ..helper_kit import mo_helper_kit


# ======== CONSTANTS ========
MODEL_FILE_NAME = "models.py"

FIELD_INSERT_MARKER = (
    "# Add model fields above this line -- "
    "(MANAGED BY MINDOFF. DO NOT TOUCH THIS LINE)"
)

VALID_ON_DELETE = {
    "CASCADE",
    "PROTECT",
    "SET_NULL",
    "SET_DEFAULT",
    "DO_NOTHING",
}


# ======== CLASSES ========
class DjangoModelFieldCreator:
    """
    Creates a single ForeignKey field inside an existing Django model.
    """

    def __init__(
        self,
        model_path: str,
        field_name: str,
        field_type: str,  # kept for CLI compatibility, ignored internally
        *,
        to: str | None = None,
        on_delete: str = "CASCADE",
        related_name_prefix: int | None = None,
        disable_related_name: bool = False,
        optional: bool = False,
        allow_blank: bool = False,  # accepted but ignored (FKs don't need it)
        unique: bool = False,
        db_index: bool = False,
        default: str | None = None,
        is_choice_field: bool = False,  # accepted but ignored
    ):
        self.model_path = model_path
        self.field_name = field_name

        self.to = to
        self.on_delete = on_delete
        self.related_name_prefix = related_name_prefix
        self.disable_related_name = disable_related_name

        self.optional = optional
        self.unique = unique
        self.db_index = db_index
        self.default = default

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

        for i in range(start + 1, len(lines)):
            if lines[i].startswith("class "):
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

        if self.on_delete not in VALID_ON_DELETE:
            raise ValueError(f"Invalid on_delete '{self.on_delete}'")

        if self.on_delete == "SET_DEFAULT" and self.default is None:
            raise ValueError(
                "--default <value> is required when on_delete is SET_DEFAULT"
            )

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

        # Auto-generate field name from parent model name if blank
        if not self.field_name:
            # OrderItemModel → order_item_ref
            base = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_model).lower()
            base = base.replace("_model", "")
            self.field_name = f"{base}_ref"
            print(f"[OK] Auto-generated field name: '{self.field_name}'")

        # Check for duplicate field in target model block
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
    def _build_related_name(self) -> str:
        if self.disable_related_name:
            return "+"

        prefix = (
            str(self.related_name_prefix)
            if self.related_name_prefix is not None
            else self.app_slug
        )

        return (
            f"{prefix}_"
            f"{self.model_name.replace('Model', '').lower()}_"
            f"{self.field_name}_rev"
        )

    def _build_foreign_key_field(self) -> str:
        related_name = self._build_related_name()

        # String reference: "app_label.ModelName" — avoids cross-app imports
        target_model = f'"{self.parent_app}.{self.parent_model}"'

        options = [
            f"on_delete=models.{self.on_delete}",
            f"related_name='{related_name}'",
            f"db_column='{self.field_name}_id'",
        ]

        if self.optional:
            options.append("null=True")
        if self.unique:
            options.append("unique=True")
        if self.db_index:
            options.append("db_index=True")
        if self.default is not None:
            options.append(f"default={self.default}")

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
            field_type=args.field_type,
            to=args.to,
            on_delete=args.on_delete,
            related_name_prefix=args.related_name_prefix,
            disable_related_name=args.disable_related_name,
            optional=args.optional,
            allow_blank=args.allow_blank,
            unique=args.unique,
            db_index=args.db_index,
            default=args.default,
            is_choice_field=args.is_choice_field,
        ).run()

    parser = subparsers.add_parser(
        "create_model_field",
        help="Create a single field inside a Django model",
    )

    parser.add_argument(
        "model_path",
        help="<app_name>/<ModelName>",
    )
    parser.add_argument(
        "field_name",
        help="Field name to create",
    )
    parser.add_argument(
        "field_type",
        choices=["foreign_key"],
        help="Type of field to create",
    )

    parser.add_argument(
        "--to",
        help="Target model for ForeignKey (<app>/<Model>)",
    )
    parser.add_argument(
        "--on_delete",
        default="CASCADE",
        help="on_delete behavior (default: CASCADE)",
    )
    parser.add_argument(
        "--related_name_prefix",
        type=int,
        help="Numeric prefix for related_name",
    )
    parser.add_argument(
        "--disable_related_name",
        action="store_true",
        help="Disable reverse relation (related_name='+')",
    )
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Allow NULL values (sets null=True)",
    )

    parser.add_argument(
        "--allow_blank",
        action="store_true",
        help="Allow blank values (blank=True)",
    )

    parser.add_argument(
        "--unique",
        action="store_true",
        help="Enforce unique constraint",
    )

    parser.add_argument(
        "--db_index",
        action="store_true",
        help="Create database index",
    )

    parser.add_argument(
        "--default",
        help="Default value for the field (required for SET_DEFAULT)",
    )

    parser.add_argument(
        "--is_choice_field",
        action="store_true",
        help="Mark field as a choices field",
    )

    parser.set_defaults(handler=_create_model_field)
