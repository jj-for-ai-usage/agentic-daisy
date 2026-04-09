"""Memory tools -- save, search, delete, list, taxonomy, drawers, graph."""
from __future__ import annotations

from claude_api.memory import MemoryStore
from . import (
    save_memory,
    search_memory,
    delete_memory,
    list_memories,
    list_wings,
    list_rooms,
    get_taxonomy,
    add_drawer,
    get_drawer,
    traverse,
    find_tunnels,
    recall,
)

# Tools whose handler is a direct MemoryStore method -- use make_handler().
_SIMPLE_TOOLS = [
    save_memory,
    search_memory,
    delete_memory,
    list_memories,
    list_wings,
    list_rooms,
    get_taxonomy,
    add_drawer,
    get_drawer,
]

# Tools that need to close over the store (palace graph traversal, L2 recall).
_BOUND_TOOLS = [traverse, find_tunnels, recall]

ALL_TOOLS = _SIMPLE_TOOLS + _BOUND_TOOLS


def register(config, registry, **kwargs):
    mem = MemoryStore(config.memory_dir)
    for mod in ALL_TOOLS:
        h = mod.make_handler(memory_store=mem)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
    # Stash the store on the registry so other loaders (e.g. memory stack)
    # can reuse it without instantiating a second copy.
    registry._memory_store = mem  # noqa: SLF001
