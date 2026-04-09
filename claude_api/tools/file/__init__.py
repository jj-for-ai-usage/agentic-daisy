"""File I/O tools -- read, write, edit, append, read_slice, tail_file, apply_patch."""
from __future__ import annotations

from . import (
    read_file, write_file, edit_file, append_file,
    read_slice, tail_file, apply_patch,
)

ALL_TOOLS = [
    read_file, write_file, edit_file, append_file,
    read_slice, tail_file, apply_patch,
]


def register(config, registry, **kwargs):
    for mod in ALL_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
