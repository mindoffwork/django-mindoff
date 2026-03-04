import re
from pathlib import Path

from ..helper_kit import mo_helper_kit

# ======== CONSTANTS =======
MODEL_FILE_NAME = "models.py"


# ======== CLASSES =======
class DjangoModelCreator:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.original_app_name = None
        self.normalized_app_name = None
        self.raw_model = None
        self.final_model_name = None
        self.base_name = None
        self.parent_class = None
        self.app = None

    def _normalize_app_name(self, dotted_path: str) -> str:
        if not dotted_path.startswith("apps."):
            dotted_path = f"apps.{dotted_path}"
        app_names = dotted_path.split(".")
        if len(app_names) != 2:
            raise ValueError(f"Invalid App Name or Path '{dotted_path}'")
        normalized = [app_names[0]] + [p.lower() for p in app_names[1:]]
        if any(not p for p in normalized):
            raise ValueError(
                f"Invalid path '{dotted_path}': segments cannot be empty after normalization."
            )
        return ".".join(normalized)

    def _parse_input(self):
        try:
            self.original_app_name, self.raw_model = self.model_path.split("/")
            self.app_slug = self.original_app_name.lower()
        except ValueError:
            print("[ERROR] Model path must be in format <app_name>/<model_name>")
            return False
        self.normalized_app_name = self._normalize_app_name(self.original_app_name)
        self.app = self.normalized_app_name
        self.final_model_name, changes = self._format_model_name(self.raw_model)
        if changes:
            print(f"[OK] Generated model name: '{self.final_model_name}'")
        self.base_name = self.raw_model.lower().replace("model", "")
        return True

    def _format_model_name(self, name):
        pascal = re.sub(r"(?:^|_)([a-z])", lambda x: x.group(1).upper(), name)
        final = pascal if pascal.endswith("Model") else f"{pascal}Model"
        changes = []
        if name != pascal:
            changes.append(f"PascalCase: '{name}' → '{pascal}'")
        if not pascal.endswith("Model"):
            changes.append(f"Added 'Model' suffix: '{pascal}' → '{final}'")
        return final, changes

    def _get_base_model(self):
        self.parent_class = "mindoff_models.TimeStampModel"
        print(f"[OK] Class set to {self.parent_class}")

    def _generate_files(self):
        model_path = Path(self.app.replace(".", "/")) / MODEL_FILE_NAME
        serializer_path = Path(self.app.replace(".", "/")) / "serializers.py"

        # Only primary id field, no foreign keys -- {self.base_name}_id
        fields_code = 'id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")\n    # Add model fields above this line -- (MANAGED BY MINDOFF. DO NOT TOUCH THIS LINE)'
        model_code = f"""
class {self.final_model_name}({self.parent_class}):
    {fields_code}

    class Meta:
        db_table = 'tbl_{self.app_slug}_{self.base_name}'

    def __str__(self):
        return str(self.id)
""".strip()

        serializer_code = f"""
class {self.final_model_name}Serializer(serializers.ModelSerializer):
    class Meta:
        model = models.{self.final_model_name}
        fields = '__all__'
""".strip()

        self._append_to_file(
            model_path, model_code, self.final_model_name, [], kind="models"
        )
        self._append_to_file(
            serializer_path,
            serializer_code,
            f"{self.final_model_name}Serializer",
            [],
            kind="serializers",
        )

    def _append_to_file(
        self, path: Path, content: str, check_class: str, import_tuples, kind: str
    ):
        if kind not in ("models", "serializers"):
            raise ValueError(f"❌ Invalid kind '{kind}'")
        base_import, imports = self._generate_imports(kind, import_tuples)
        if path.exists():
            text = path.read_text()
            if f"class {check_class}(" in text:
                print(
                    f"[ACTION] Class '{check_class}' already exists in {path.name}. Skipping."
                )
                return
            lines = text.splitlines()
            existing_imports = {
                l.strip() for l in lines if l.strip().startswith("from")
            }
            new_imports = [line for line in imports if line not in existing_imports]
            insert_index = next(
                (
                    i + 1
                    for i, line in enumerate(lines)
                    if line.strip().startswith(("from", "import"))
                ),
                0,
            )
            if new_imports:
                lines[insert_index:insert_index] = new_imports
            lines.append("")
            lines.append(content)
            path.write_text("\n".join(lines))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [base_import] + imports + ["", content]
            path.write_text("\n".join(lines))
        print(f"[OK] Written to {path}")

    def _generate_imports(self, kind: str, import_tuples):
        if kind == "models":
            imports = [
                f"from {p} import models as {alias}" for p, alias in import_tuples
            ]
            base_import = "from django.db import models"
        else:  # serializers
            imports = [
                f"from {p}.serializers import {s} as {alias}_serializer"
                for p, alias, s in import_tuples
            ]
            base_import = "from rest_framework import serializers"
        return base_import, imports

    @mo_helper_kit.file_guardian
    def run(self):
        if not self._parse_input():
            return
        project_root = Path.cwd()
        app_dir = project_root / self.app.replace(".", "/")
        if not app_dir.exists():
            print(f"[ERROR] App directory '{app_dir}' doesn't exist")
            return
        self._get_base_model()
        self._generate_files()


# ======== FUNCTIONS =======
def register_subcommand(subparsers):
    def _create_model(args):
        DjangoModelCreator(args.model_path).run()

    parser = subparsers.add_parser(
        "createmodel", help="Create Django model (no foreign keys)"
    )
    parser.add_argument(
        "model_path", help="New Model path in format <app_name>/<ModelName>"
    )
    parser.set_defaults(handler=_create_model)
