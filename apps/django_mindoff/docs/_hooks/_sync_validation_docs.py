from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import Any


def _clean_docstring(value: str | None) -> str:
    if not value:
        return ""
    return textwrap.dedent(value).strip()


def _get_class_node(tree: ast.AST, class_name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _render_validator_methods_markdown(class_node: ast.ClassDef) -> str:
    methods = [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]
    lines: list[str] = []
    index = 0
    for node in methods:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        index += 1
        human_name = node.name.replace("_", " ").title()
        lines.extend(
            [
                f"### {index}. {human_name}",
                "",
                _clean_docstring(ast.get_docstring(node)),
                "",
            ]
        )
    return "\n".join(lines).strip()


def _build_validation_token_markdown(config: Any) -> str | None:
    config_file = Path(config.get("config_file_path", "mkdocs.yml")).resolve()
    root = config_file.parent
    source_path = root / "apps" / "django_mindoff" / "components" / "validation_kit.py"
    if not source_path.exists():
        return None
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    validator_node = _get_class_node(tree, "MindoffValidator")
    if validator_node is None:
        return None
    return _render_validator_methods_markdown(validator_node)


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    token = "{{ MO_VALIDATION_KIT_METHODS }}"
    if token not in markdown:
        return markdown
    cfg = config or {}
    snippet = _build_validation_token_markdown(cfg)
    if not snippet:
        return markdown
    return markdown.replace(token, snippet.strip())
