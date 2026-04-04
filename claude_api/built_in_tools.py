"""Agentic Daisy — Built-in tool registration and system prompt."""
from __future__ import annotations

from .config import DaisyConfig
from .file_tools import list_directory, read_file, write_file
from .memory import MemoryStore
from .tool_registry import ToolRegistry


def build_default_system_prompt(registry: ToolRegistry) -> str:
    """Build a system prompt describing the environment and available tools."""
    tools = registry.list_tool_summaries()
    tool_lines = "\n".join(
        "- %s: %s" % (t["name"], t["description"]) for t in tools
    )
    return (
        "You are Daisy, a Claude-based assistant running on an enterprise "
        "server (RHEL, air-gapped with outbound proxy). You help with system "
        "administration, scripting, file management, and general questions.\n"
        "\n"
        "You have the following tools available:\n"
        "%s\n"
        "\n"
        "Guidelines:\n"
        "- Use tools proactively when they help answer the user's question.\n"
        "- Use save_memory to persist important context across sessions.\n"
        "- At the start of a conversation, search_memory or list_memories to "
        "recall prior context.\n"
        "- Be concise and direct. This is a terminal environment.\n"
        "- When running shell commands or writing files, describe what you are "
        "doing and why before executing." % tool_lines
    )


def create_default_registry(config: DaisyConfig) -> ToolRegistry:
    """Create a ToolRegistry pre-loaded with all built-in tools."""
    registry = ToolRegistry()

    # -- Memory tools --
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
        name="delete_memory",
        description="Delete a saved memory by its key.",
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key of the memory to delete",
                },
            },
            "required": ["key"],
        },
        handler=mem.delete_memory,
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

    # -- File tools --

    registry.register(
        name="read_file",
        description="Read the contents of a file. Returns up to 100K characters.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to read",
                },
            },
            "required": ["path"],
        },
        handler=read_file,
    )

    registry.register(
        name="write_file",
        description=(
            "Write content to a file. Creates parent directories if needed. "
            "Overwrites existing content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=write_file,
    )

    registry.register(
        name="list_directory",
        description="List directory contents with file types and sizes.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory path to list (default: current directory)"
                    ),
                },
            },
        },
        handler=list_directory,
    )

    return registry
