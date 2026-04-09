"""System tools -- environment info, skill loading, skill/tool creation, repair, list_tools, planning."""
from __future__ import annotations

from typing import Any, Dict, List

from . import (
    get_env, load_skill, create_skill, create_tool, repair_tools, repair_skills,
    list_tools, plan_write, plan_show,
)

# Tools with plain handlers
STANDALONE_TOOLS = [get_env]

# Tools needing context (skill_loader, config, registry)
CONTEXT_TOOLS = [load_skill, create_skill, create_tool, repair_tools, repair_skills]

# Tools needing only the registry (no skill_loader dependency)
REGISTRY_TOOLS = [list_tools]

# Tools sharing a session-scoped Planner instance
PLANNER_TOOLS = [plan_write, plan_show]


class _Planner:
    """In-memory, session-scoped plan store shared by plan_write and plan_show.

    NOT persisted to disk. The plan dies at session end. For durable
    multi-session work, use the task tools instead.
    """

    def __init__(self) -> None:
        self._steps: List[Dict[str, Any]] = []

    def replace(self, steps: List[Dict[str, Any]]) -> None:
        # Normalize: keep only content + status
        self._steps = [
            {"content": str(s["content"]), "status": str(s["status"])}
            for s in steps
        ]

    def get(self) -> List[Dict[str, Any]]:
        return list(self._steps)


def register(config, registry, skill_loader=None, **kwargs):
    for mod in STANDALONE_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
    for mod in REGISTRY_TOOLS:
        h = mod.make_handler(registry=registry, config=config)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
    # Planner tools share one in-memory instance
    planner = _Planner()
    for mod in PLANNER_TOOLS:
        h = mod.make_handler(planner=planner)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
    if skill_loader is not None:
        for mod in CONTEXT_TOOLS:
            h = mod.make_handler(
                skill_loader=skill_loader, config=config, registry=registry,
            )
            registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
