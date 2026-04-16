"""Tool: list_memories — list all saved memory keys with metadata."""
NAME = "list_memories"
DESCRIPTION = (
    "List all saved memory keys with their tags and last-updated timestamps. "
    "Call when starting work on a named customer or project to recall prior "
    "context (project paths, tool versions, prior decisions) before "
    "re-discovering things from scratch."
)
INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_handler(memory_store=None, **kwargs):
    return memory_store.list_memories
