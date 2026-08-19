from __future__ import annotations

from pathlib import Path
import re
from typing import Any


TOKEN = "{{ COMMUNITY_MENU }}"


def __parse_link_entry(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s{6}-\s+(.+?):\s+(.+?)\s*$", line)
    if not m:
        return None

    title = m.group(1).strip().strip('"').strip("'")
    path = m.group(2).strip().strip('"').strip("'")

    if not path.startswith("community/") or not path.endswith(".md"):
        return None

    if path.endswith("/index.md"):
        return None

    rel_path = path.split("community/", 1)[1]
    return (title, rel_path)


def _extract_community_links(config_file: Path) -> list[tuple[str, str]]:
    lines = config_file.read_text(encoding="utf-8-sig").splitlines()
    in_nav = False
    in_community = False
    links: list[tuple[str, str]] = []

    for line in lines:
        stripped = line.strip()

        if not in_nav:
            if stripped == "nav:":
                in_nav = True
            continue

        if not in_community:
            if re.match(r"^\s{2}-\s+Community:\s*$", line):
                in_community = True
            continue

        # End of Community block (next top-level nav group)
        if re.match(r"^\s{2}-\s+[^#].*:\s*$", line):
            break

        # Parse child entries under Community
        link_entry = __parse_link_entry(line)
        if link_entry:
            links.append(link_entry)

    return links


def _render_menu_markdown(links: list[tuple[str, str]]) -> str:
    if not links:
        return ""
    return "\n".join(f"- [{title}]({path})" for title, path in links)


def on_page_markdown(markdown: str, config: Any = None, **kwargs: Any) -> str:
    if TOKEN not in markdown:
        return markdown

    cfg = config or {}
    root = Path(cfg.get("config_file_path", "mkdocs.yml")).resolve().parent
    config_file = root / "mkdocs.yml"
    if not config_file.exists():
        return markdown

    links = _extract_community_links(config_file)
    menu_md = _render_menu_markdown(links)
    return markdown.replace(TOKEN, menu_md)
