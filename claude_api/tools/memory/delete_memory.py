"""Tool: delete_memory — remove a saved memory by key."""
NAME = "delete_memory"
DESCRIPTION = "Delete a saved memory by its key."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "The key of the memory to delete"},
    },
    "required": ["key"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.delete_memory
