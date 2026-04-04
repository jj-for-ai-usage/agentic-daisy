"""Execution tools — run_command, run_python."""
from __future__ import annotations

from . import run_command, run_python

ALL_TOOLS = [run_command, run_python]


def register(config, registry, audit=None, interactive=False, workspace_dir="", **kwargs):
    for mod in ALL_TOOLS:
        h = mod.make_handler(audit=audit, interactive=interactive, workspace_dir=workspace_dir)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
