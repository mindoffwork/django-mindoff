from __future__ import annotations

from importlib import util
from pathlib import Path
from types import ModuleType
from typing import Any


_LOADED_HOOKS: list[ModuleType] | None = None


def _load_hook_module(path: Path, index: int) -> ModuleType | None:
    module_name = f"_docs_hook_{index}_{path.stem}"
    spec = util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_loaded_hooks() -> list[ModuleType]:
    global _LOADED_HOOKS
    if _LOADED_HOOKS is not None:
        return _LOADED_HOOKS

    base_dir = Path(__file__).resolve().parent
    modules: list[ModuleType] = []
    hook_files = sorted(base_dir.glob("_sync_*.py"), key=lambda p: p.name)
    for idx, file_path in enumerate(hook_files):
        module = _load_hook_module(file_path, idx)
        if module is not None:
            modules.append(module)
    _LOADED_HOOKS = modules
    return modules


def on_config(config: Any) -> Any:
    current = config
    for module in _get_loaded_hooks():
        hook = getattr(module, "on_config", None)
        if hook is None:
            continue
        result = hook(current)
        if result is not None:
            current = result
    return current


def on_pre_build(config: Any) -> None:
    for module in _get_loaded_hooks():
        hook = getattr(module, "on_pre_build", None)
        if hook is None:
            continue
        hook(config)


def on_page_markdown(markdown: str, **kwargs: Any) -> str:
    rendered = markdown
    for module in _get_loaded_hooks():
        hook = getattr(module, "on_page_markdown", None)
        if hook is None:
            continue
        rendered = hook(rendered, **kwargs)
    return rendered
