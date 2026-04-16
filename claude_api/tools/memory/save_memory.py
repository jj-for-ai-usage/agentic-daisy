"""Tool: save_memory — persist information across conversations."""
NAME = "save_memory"
DESCRIPTION = (
    "Save a piece of information for retrieval across sessions. "
    "Use for: customer project paths, tool versions, known workarounds, "
    "timing targets, constraint file paths, directory layouts. "
    "Tag by customer and project (e.g. tags=['acme', 'cpu_top']) so "
    "search_memory(tag=...) can retrieve all related entries."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": (
                "Short identifier, kebab-case "
                "(e.g. 'acme-cpu_top-timing-target')"
            ),
        },
        "value": {"type": "string", "description": "The content to remember"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Categorization tags — at minimum include the customer "
                "and project, e.g. ['acme', 'cpu_top']"
            ),
        },
    },
    "required": ["key", "value"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.save_memory
