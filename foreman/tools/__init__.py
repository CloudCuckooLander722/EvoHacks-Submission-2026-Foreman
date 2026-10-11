"""Auto-discovering registry: get_tool(), catalog().

Every module in `foreman/tools/` (except `base`) is imported on first use and each
concrete `Tool` subclass with a `name` is registered. Nothing here needs editing when a
tool is added, so tools can be built in parallel without merge conflicts.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from foreman.tools.base import Tool

log = logging.getLogger(__name__)

_registry: dict[str, type[Tool]] | None = None


def _subclasses(cls: type) -> list[type]:
    out: list[type] = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out.extend(_subclasses(sub))
    return out


def _discover() -> dict[str, type[Tool]]:
    global _registry
    if _registry is not None:
        return _registry
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "base" or mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{mod.name}")
        except Exception:
            log.exception("could not import tool module %s", mod.name)
    found: dict[str, type[Tool]] = {}
    for cls in _subclasses(Tool):
        name = getattr(cls, "name", None)
        if not cls.__module__.startswith(f"{__name__}.") or cls.__module__ == f"{__name__}.base":
            continue
        if inspect.isabstract(cls) or not isinstance(name, str) or not name:
            continue
        if name in found and found[name] is not cls:
            log.error(
                "duplicate tool name %r in %s and %s", name, found[name].__module__, cls.__module__
            )
            continue
        found[name] = cls
    _registry = dict(sorted(found.items()))
    return _registry


def get_tool(name: str) -> Tool:
    """A fresh instance of the named tool. Unknown names list the valid ones."""
    reg = _discover()
    if name not in reg:
        raise KeyError(f"unknown tool {name!r}; valid tools: {', '.join(reg) or '(none)'}")
    return reg[name]()


def all_tools() -> list[Tool]:
    """An instance of every registered tool, sorted by name."""
    return [cls() for cls in _discover().values()]


def catalog() -> list[dict[str, Any]]:
    """What the planner sees: name, description, uses and the params JSON schema."""
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "uses": cls.uses,
            "params_schema": cls.Params.model_json_schema(),
        }
        for cls in _discover().values()
    ]


__all__ = ["all_tools", "catalog", "get_tool"]
