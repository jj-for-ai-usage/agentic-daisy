"""Knowledge-graph tools -- temporal triples with validity windows."""
from __future__ import annotations

from claude_api.kg_store import KGStore
from . import kg_add, kg_query, kg_invalidate, kg_timeline, kg_stats

ALL_TOOLS = [kg_add, kg_query, kg_invalidate, kg_timeline, kg_stats]


def register(config, registry, **kwargs):
    kg = KGStore(config.kg_dir)
    for mod in ALL_TOOLS:
        h = mod.make_handler(kg_store=kg)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
    registry._kg_store = kg  # noqa: SLF001
