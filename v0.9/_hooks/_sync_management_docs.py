from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

TOKEN = "{{ MANAGEMENT_INIT_REQUIREMENTS }}"
PYPROJECT_RELPATH = "pyproject.toml"
OPTIONAL_GROUP = "internal"


def _read_pyproject_dependencies(pyproject_path: Path) -> list[str]:
    if not pyproject_path.exists():
        return []
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    base = project.get("dependencies", []) or []
    optional = project.get("optional-dependencies", {}).get(OPTIONAL_GROUP, []) or []

    seen = set()
    combined: list[str] = []
    for item in list(base) + list(optional):
        if item not in seen:
            combined.append(item)
            seen.add(item)
    return combined


def _build_requirements_markdown(config: Any) -> str | None:
    config_file = Path(config.get("config_file_path", "mkdocs.yml")).resolve()
    root = config_file.parent
    pyproject_path = root / PYPROJECT_RELPATH
    packages = _read_pyproject_dependencies(pyproject_path)
    if not packages:
        return None

    lines = [""]
    lines.extend(f"- `{pkg}`" for pkg in packages)
    return "\n".join(lines)


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    if TOKEN not in markdown:
        return markdown
    cfg = config or {}
    snippet = _build_requirements_markdown(cfg)
    if not snippet:
        return markdown
    return markdown.replace(TOKEN, snippet)
