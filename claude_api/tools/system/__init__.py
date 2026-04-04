"""System tools — environment info + skill loading."""
from __future__ import annotations

from . import get_env, load_skill

# Tools with plain handlers
STANDALONE_TOOLS = [get_env]

# Tools needing a skill_loader instance
CONTEXT_TOOLS = [load_skill]


def register(config, registry, skill_loader=None, **kwargs):
    for mod in STANDALONE_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
    if skill_loader is not None:
        for mod in CONTEXT_TOOLS:
            h = mod.make_handler(skill_loader=skill_loader)
            registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
