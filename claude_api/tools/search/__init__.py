"""Search and navigation tools — search_files, find_files, directory_tree, list_directory."""
from __future__ import annotations

from . import search_files, find_files, directory_tree, list_directory

ALL_TOOLS = [search_files, find_files, directory_tree, list_directory]


def register(config, registry, audit=None, **kwargs):
    for mod in ALL_TOOLS:
        if hasattr(mod, "make_handler"):
            h = mod.make_handler(audit=audit, config=config)
        else:
            h = mod.handler
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
