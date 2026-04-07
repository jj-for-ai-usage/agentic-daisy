"""File I/O tools -- read, write, edit, append."""
from __future__ import annotations

from . import read_file, write_file, edit_file, append_file

ALL_TOOLS = [read_file, write_file, edit_file, append_file]


def register(config, registry, **kwargs):
    for mod in ALL_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
