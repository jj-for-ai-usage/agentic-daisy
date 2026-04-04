"""System tools — environment info."""
from __future__ import annotations

from . import get_env

ALL_TOOLS = [get_env]


def register(config, registry, **kwargs):
    for mod in ALL_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
