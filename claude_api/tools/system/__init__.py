"""System tools — skill loading, skill/tool creation, repair."""
from __future__ import annotations

from . import load_skill, create_skill, create_tool, repair_tools, repair_skills

# Tools needing context (skill_loader, config, registry)
CONTEXT_TOOLS = [load_skill, create_skill, create_tool, repair_tools, repair_skills]


def register(config, registry, skill_loader=None, audit=None, admin=False, **kwargs):
    if skill_loader is not None:
        for mod in CONTEXT_TOOLS:
            h = mod.make_handler(
                skill_loader=skill_loader, config=config, registry=registry,
                audit=audit, admin=admin,
            )
            registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
