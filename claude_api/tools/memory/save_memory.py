"""Tool: save_memory -- persist information across conversations."""
NAME = "save_memory"
DESCRIPTION = (
    "Save a piece of information for later retrieval. "
    "Use this to remember important context, decisions, or facts "
    "across conversations."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Short identifier for this memory (e.g. 'project-status')",
        },
        "value": {"type": "string", "description": "The content to remember"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional categorization tags",
        },
    },
    "required": ["key", "value"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.save_memory
