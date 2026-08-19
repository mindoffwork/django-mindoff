from __future__ import annotations

from pathlib import Path
import re
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

PYTHON_SUPPORTED = "{{ PYTHON_SUPPORTED }}"
PYTHON_REQUIRES = "{{ PYTHON_REQUIRES }}"
_TOKENS: dict[str, str] = {
    PYTHON_SUPPORTED: "3.12 or 3.13",
    PYTHON_REQUIRES: ">=3.12",
}


def _format_versions(versions: list[str]) -> str:
    if not versions:
        return ""
    if len(versions) == 1:
        return versions[0]
    if len(versions) == 2:
        return f"{versions[0]} or {versions[1]}"
    return ", ".join(versions[:-1]) + f", or {versions[-1]}"


def _read_pyproject(config: Any) -> dict[str, str]:
    if tomllib is None:
        return _TOKENS

    config_file = Path(config.get("config_file_path", "mkdocs.yml")).resolve()
    pyproject_path = config_file.parent / "pyproject.toml"
    if not pyproject_path.exists():
        return _TOKENS

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    classifiers = project.get("classifiers", [])
    requires_python = str(project.get("requires-python", _TOKENS["{{ PYTHON_REQUIRES }}"]))

    versions: list[str] = []
    for classifier in classifiers:
        match = re.match(r"Programming Language :: Python :: (\d+\.\d+)$", str(classifier).strip())
        if match:
            versions.append(match.group(1))

    versions = sorted(set(versions), key=lambda v: tuple(int(x) for x in v.split(".")))
    supported = _format_versions(versions) or _TOKENS["{{ PYTHON_SUPPORTED }}"]

    return {
        PYTHON_SUPPORTED: supported,
        PYTHON_REQUIRES: requires_python,
    }


def on_config(config: Any) -> Any:
    global _TOKENS
    _TOKENS = _read_pyproject(config)
    return config


def on_page_markdown(markdown: str, **kwargs: Any) -> str:
    rendered = markdown
    for token, value in _TOKENS.items():
        rendered = rendered.replace(token, value)
    return rendered
