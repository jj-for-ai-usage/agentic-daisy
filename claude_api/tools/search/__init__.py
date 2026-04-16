"""Search and navigation tools — search_files, directory_tree, list_directory, list_recent_files."""
from __future__ import annotations

from . import search_files, directory_tree, list_directory, list_recent_files

ALL_TOOLS = [search_files, directory_tree, list_directory, list_recent_files]


def register(config, registry, audit=None, **kwargs):
    for mod in ALL_TOOLS:
        if hasattr(mod, "make_handler"):
            h = mod.make_handler(audit=audit, config=config)
        else:
            h = mod.handler
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
