"""EDA tools — scan_workspaces, tabulate_workspaces.

Built-in tools for Cadence SYN/PNR workspace discovery and metric tabulation.
Ported (DB-free) from github.com/jj-for-ai-usage/DAISY.
"""
from __future__ import annotations

from . import scan_workspaces, tabulate_workspaces

ALL_TOOLS = [scan_workspaces, tabulate_workspaces]


def register(config, registry, audit=None, **kwargs):
    for mod in ALL_TOOLS:
        h = mod.make_handler(audit=audit)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
