"""System tools — environment info, skill loading, skill/tool creation, repair."""
from __future__ import annotations

from . import get_env, load_skill, create_skill, create_tool, repair_tools, repair_skills

# Tools with plain handlers
STANDALONE_TOOLS = [get_env]

# Tools needing context (skill_loader, config, registry)
CONTEXT_TOOLS = [load_skill, create_skill, create_tool, repair_tools, repair_skills]


def register(config, registry, skill_loader=None, audit=None, admin=False, **kwargs):
    for mod in STANDALONE_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
    if skill_loader is not None:
        for mod in CONTEXT_TOOLS:
            h = mod.make_handler(
                skill_loader=skill_loader, config=config, registry=registry,
                audit=audit, admin=admin,
            )
            registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
