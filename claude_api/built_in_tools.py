"""Agentic Daisy — Built-in tool registration (memory tools)."""
from __future__ import annotations

from .config import DaisyConfig
from .memory import MemoryStore
from .tool_registry import ToolRegistry


def create_default_registry(config: DaisyConfig) -> ToolRegistry:
    """Create a ToolRegistry pre-loaded with all built-in tools."""
    registry = ToolRegistry()
    mem = MemoryStore(config.memory_dir)

    registry.register(
        name="save_memory",
        description=(
            "Save a piece of information for later retrieval. "
            "Use this to remember important context, decisions, or facts "
            "across conversations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Short identifier for this memory "
                        "(e.g. 'project-status', 'user-preference-timezone')"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "The content to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional categorization tags",
                },
            },
            "required": ["key", "value"],
        },
        handler=mem.save_memory,
    )

    registry.register(
        name="search_memory",
        description=(
            "Search saved memories by keyword (matches against keys and values) "
            "or by tag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (substring match on key and value)",
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by exact tag match",
                },
            },
        },
        handler=mem.search_memory,
    )

    registry.register(
        name="list_memories",
        description=(
            "List all saved memory keys with their tags and "
            "last-updated timestamps."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=mem.list_memories,
    )

    return registry
