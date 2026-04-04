"""Agentic Daisy — Tool loading and discovery."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_api.audit import AuditLogger
    from claude_api.config import DaisyConfig
    from claude_api.tool_registry import ToolRegistry


def load_all_tools(config: DaisyConfig, registry: ToolRegistry) -> None:
    """Load all built-in tools (everything except execution tools)."""
    from claude_api.skills import SkillLoader
    from .memory import register as reg_memory
    from .file import register as reg_file
    from .search import register as reg_search
    from .system import register as reg_system
    reg_memory(config, registry)
    reg_file(config, registry)
    reg_search(config, registry)
    # System tools get a SkillLoader for load_skill
    skill_loader = SkillLoader(config.skills_dir)
    reg_system(config, registry, skill_loader=skill_loader)


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
