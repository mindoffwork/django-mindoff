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


def _render_polars_methods_markdown(class_node: ast.ClassDef) -> str:
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


def _render_helper_functions_markdown(tree: ast.Module) -> str:
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]
    lines: list[str] = []
    index = 0
    for node in functions:
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


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    cfg = config or {}
    root = Path(cfg.get("config_file_path", "mkdocs.yml")).resolve().parent

    polars_token = "{{ MO_POLARS_KIT_FUNCTIONS }}"
    helper_token = "{{ MO_HELPER_KIT_FUNCTIONS }}"

    if polars_token in markdown:
        source_path = root / "apps" / "django_mindoff" / "components" / "polars_kit.py"
        if source_path.exists():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            kit_node = _get_class_node(tree, "MindoffPolarsKit")
            if kit_node is not None:
                markdown = markdown.replace(
                    polars_token, _render_polars_methods_markdown(kit_node)
                )

    if helper_token in markdown:
        source_path = root / "apps" / "django_mindoff" / "components" / "helper_kit.py"
        if source_path.exists():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            markdown = markdown.replace(
                helper_token, _render_helper_functions_markdown(tree)
            )

    return markdown
