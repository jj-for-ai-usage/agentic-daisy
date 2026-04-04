"""Agentic Daisy — Tool loading and discovery."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_api.audit import AuditLogger
    from claude_api.config import DaisyConfig
    from claude_api.tool_registry import ToolRegistry


def load_all_tools(config: DaisyConfig, registry: ToolRegistry) -> None:
    """Load all built-in tools (everything except execution tools)."""
    from claude_api.config import USER_SKILLS_DIR, USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR
    from claude_api.custom_tools import CustomToolLoader
    from claude_api.skills import SkillLoader
    from claude_api.task_store import TaskStore
    from .memory import register as reg_memory
    from .file import register as reg_file
    from .search import register as reg_search
    from .system import register as reg_system
    reg_memory(config, registry)
    reg_file(config, registry)
    reg_search(config, registry)
    # System tools get a SkillLoader (user-level first, project overrides)
    skill_loader = SkillLoader([USER_SKILLS_DIR, config.skills_dir])
    reg_system(config, registry, skill_loader=skill_loader)
    # Shared TaskStore instance for task + batch tools (avoid stale copies)
    task_store = TaskStore(config.task_dir)
    # Task tools
    from .task import register as reg_task
    reg_task(config, registry, task_store=task_store)
    # Batch tools
    from .batch import register as reg_batch
    reg_batch(config, registry, task_store=task_store)
    # Load custom tools from ~/.daisy/tools/ and .daisy/tools/
    CustomToolLoader([USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR]).load_into(registry)


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
