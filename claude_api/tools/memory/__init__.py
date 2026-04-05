"""Memory tools -- save, search, delete, list."""
from __future__ import annotations

from claude_api.memory import MemoryStore
from . import save_memory, search_memory, delete_memory, list_memories

ALL_TOOLS = [save_memory, search_memory, delete_memory, list_memories]


def register(config, registry, **kwargs):
    mem = MemoryStore(config.memory_dir)
    for mod in ALL_TOOLS:
        h = mod.make_handler(memory_store=mem) if hasattr(mod, "make_handler") else mod.handler
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
