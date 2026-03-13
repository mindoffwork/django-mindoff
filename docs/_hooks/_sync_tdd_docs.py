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


def _render_mindoff_testcase_helpers_markdown(class_node: ast.ClassDef) -> str:
    helpers = [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and (node.name == "_asserts" or node.name.startswith("_mo_"))
    ]
    lines: list[str] = []
    for index, node in enumerate(helpers, start=1):
        human_name = node.name.lstrip("_").replace("_", " ").title()
        human_name = human_name.replace("Mo ", "").title()
        lines.extend(
            [
                f"### {index}. {human_name}",
                "",
                _clean_docstring(ast.get_docstring(node)),
                "",
            ]
        )
    return "\n".join(lines).strip()


def _build_tdd_token_markdown(config: Any) -> str | None:
    config_file = Path(config.get("config_file_path", "mkdocs.yml")).resolve()
    root = config_file.parent
    source_path = root / "apps" / "django_mindoff" / "components" / "tdd_kit.py"
    if not source_path.exists():
        return None
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    test_case_node = _get_class_node(tree, "MindoffTestCase")
    if test_case_node is None:
        return None
    return _render_mindoff_testcase_helpers_markdown(test_case_node)


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    token = "{{ MINDOFF_TESTCASE_HELPERS }}"
    if token not in markdown:
        return markdown
    cfg = config or {}
    snippet = _build_tdd_token_markdown(cfg)
    if not snippet:
        return markdown
    return markdown.replace(token, snippet.strip())
