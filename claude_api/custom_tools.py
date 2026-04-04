"""Agentic Daisy — Custom tool loader (dynamic import from .py files)."""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from claude_api.tool_registry import ToolRegistry

LOG = logging.getLogger("daisy")

_REQUIRED_ATTRS = ("NAME", "DESCRIPTION", "INPUT_SCHEMA", "handler")


class CustomToolLoader:
    """Scan directories for .py tool files and register them dynamically.

    Directories are scanned in order — later directories override earlier
    ones for the same tool name (project-level overrides user-level).
    """

    def __init__(self, tool_dirs: List[str]) -> None:
        self.tool_dirs = tool_dirs

    def load_into(self, registry: ToolRegistry) -> int:
        """Import .py files from all dirs, register valid tools. Returns count."""
        count = 0
        for d in self.tool_dirs:
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".py") or fname.startswith("_"):
                    continue
                path = os.path.join(d, fname)
                try:
                    mod = self._import_file(path)
                    for attr in _REQUIRED_ATTRS:
                        if not hasattr(mod, attr):
                            raise AttributeError("Missing required attribute: %s" % attr)
                    # Ensure INPUT_SCHEMA has "type" (required by Anthropic API)
                    schema = mod.INPUT_SCHEMA
                    if not isinstance(schema, dict) or "type" not in schema:
                        raise ValueError(
                            "INPUT_SCHEMA must be a dict with 'type' field "
                            "(e.g. {\"type\": \"object\", ...})"
                        )
                    registry.register(mod.NAME, mod.DESCRIPTION,
                                      schema, mod.handler)
                    LOG.debug("Loaded custom tool '%s' from %s", mod.NAME, path)
                    count += 1
                except Exception as exc:
                    LOG.warning("Skipping custom tool %s: %s", fname, exc)
        return count

    @staticmethod
    def _import_file(path: str):
        """Dynamically import a .py file as a module."""
        name = "_daisy_custom_" + os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
