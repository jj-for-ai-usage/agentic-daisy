"""File I/O tools — read, write, edit, append, stat, delete."""
from __future__ import annotations

from . import (
    read_file, write_file, edit_file, append_file, stat_file, delete_file,
)

ALL_TOOLS = [
    read_file, write_file, edit_file, append_file, stat_file, delete_file,
]


def register(config, registry, audit=None, **kwargs):
    for mod in ALL_TOOLS:
        # Each tool either exposes a plain `handler` or a `make_handler`
        # factory that accepts shared context. Dispatch uniformly.
        if hasattr(mod, "make_handler"):
            h = mod.make_handler(audit=audit, config=config)
        else:
            h = mod.handler
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
