from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import Any

TOKEN = "{{ MO_API_PROGRESS_CHECKPOINT }}"
SOURCE_RELPATH = "apps/django_mindoff/components/api_kit.py"
CLASS_NAME = "MindoffAPIMixin"
METHOD_NAME = "progress_checkpoint"


def _clean_docstring(value: str | None) -> str:
    if not value:
        return ""
    return textwrap.dedent(value).strip()


def _build_progress_checkpoint_markdown(config: Any) -> str | None:
    config_file = Path(config.get("config_file_path", "mkdocs.yml")).resolve()
    root = config_file.parent
    source_path = root / SOURCE_RELPATH
    if not source_path.exists():
        return None

    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    method_node = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != CLASS_NAME:
            continue
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name == METHOD_NAME:
                method_node = child
                break
        break
    if method_node is None:
        return None

    method_doc = _clean_docstring(ast.get_docstring(method_node))
    return "\n".join(
        [
            "",
            method_doc,
            "",
        ]
    )


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    if TOKEN not in markdown:
        return markdown
    cfg = config or {}
    snippet = _build_progress_checkpoint_markdown(cfg)
    if not snippet:
        return markdown
    return markdown.replace(TOKEN, snippet.strip())
