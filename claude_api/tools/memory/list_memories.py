"""Tool: list_memories -- list all saved memory keys with metadata."""
NAME = "list_memories"
DESCRIPTION = "List all saved memory keys with their tags and last-updated timestamps."
INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_handler(memory_store=None, **kwargs):
    return memory_store.list_memories
