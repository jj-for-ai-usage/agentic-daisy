"""System tools -- environment info, skill loading, skill/tool creation, repair, list_tools."""
from __future__ import annotations

from . import (
    get_env, load_skill, create_skill, create_tool, repair_tools, repair_skills,
    list_tools,
)

# Tools with plain handlers
STANDALONE_TOOLS = [get_env]

# Tools needing context (skill_loader, config, registry)
CONTEXT_TOOLS = [load_skill, create_skill, create_tool, repair_tools, repair_skills]

# Tools needing only the registry (no skill_loader dependency)
REGISTRY_TOOLS = [list_tools]


def register(config, registry, skill_loader=None, **kwargs):
    for mod in STANDALONE_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
    for mod in REGISTRY_TOOLS:
        h = mod.make_handler(registry=registry, config=config)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
    if skill_loader is not None:
        for mod in CONTEXT_TOOLS:
            h = mod.make_handler(
                skill_loader=skill_loader, config=config, registry=registry,
            )
            registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
