"""Agentic Daisy -- Tool loading and discovery."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_api.audit import AuditLogger
    from claude_api.config import DaisyConfig
    from claude_api.tool_registry import ToolRegistry

LOG = logging.getLogger("daisy")


def load_all_tools(config: DaisyConfig, registry: ToolRegistry) -> None:
    """Load all built-in tools (everything except execution tools).

    Each category is loaded independently so a failure in one doesn't
    prevent the others from registering.
    """
    from claude_api.config import USER_SKILLS_DIR, USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR
    from claude_api.custom_tools import CustomToolLoader
    from claude_api.skills import SkillLoader
    from claude_api.task_store import TaskStore

    from .memory import register as reg_memory
    from .file import register as reg_file
    from .search import register as reg_search
    from .system import register as reg_system

    # --- Core tool categories (each isolated) ---
    for name, loader in [
        ("memory", lambda: reg_memory(config, registry)),
        ("file",   lambda: reg_file(config, registry)),
        ("search", lambda: reg_search(config, registry)),
    ]:
        try:
            loader()
        except Exception as exc:
            LOG.error("Failed to load '%s' tools: %s", name, exc)

    # System tools need a SkillLoader
    try:
        skill_loader = SkillLoader([USER_SKILLS_DIR, config.skills_dir])
        reg_system(config, registry, skill_loader=skill_loader)
    except Exception as exc:
        LOG.error("Failed to load 'system' tools: %s", exc)

    # Shared TaskStore for task + batch tools
    try:
        task_store = TaskStore(config.task_dir)
    except Exception as exc:
        LOG.error("Failed to init TaskStore: %s", exc)
        task_store = None

    if task_store is not None:
        for name, loader in [
            ("task",  lambda: __import__("claude_api.tools.task", fromlist=["register"]).register(config, registry, task_store=task_store)),
            ("batch", lambda: __import__("claude_api.tools.batch", fromlist=["register"]).register(config, registry, task_store=task_store)),
        ]:
            try:
                loader()
            except Exception as exc:
                LOG.error("Failed to load '%s' tools: %s", name, exc)

    # Custom tools from ~/.daisy/tools/ and .daisy/tools/
    try:
        CustomToolLoader([USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR]).load_into(registry)
    except Exception as exc:
        LOG.error("Failed to load custom tools: %s", exc)


def load_execution_tools(
    config: DaisyConfig,
    registry: ToolRegistry,
    audit: AuditLogger,
    interactive: bool = False,
    workspace_dir: str = "",
) -> None:
    """Load execution tools (run_command, run_python). Called separately by CLI."""
    from .execution import register as reg_exec
    reg_exec(config, registry, audit=audit, interactive=interactive,
             workspace_dir=workspace_dir)
