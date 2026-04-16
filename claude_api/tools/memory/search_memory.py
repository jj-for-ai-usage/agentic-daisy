"""Tool: search_memory — find saved memories by keyword or tag."""
NAME = "search_memory"
DESCRIPTION = (
    "Search saved memories by keyword (substring match on keys and values) "
    "or by tag. Call before save_memory to avoid duplicate entries, and "
    "when the user mentions a customer/project/block name to recall prior "
    "context tagged that way."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search term (substring match on key and value)"},
        "tag": {"type": "string", "description": "Filter by exact tag match"},
    },
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.search_memory
