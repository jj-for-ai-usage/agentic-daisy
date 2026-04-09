"""Tool: get_drawer -- return the full content of an archived drawer."""
NAME = "get_drawer"
DESCRIPTION = (
    "Read the verbatim content of a drawer by its drawer_id. Returns the "
    "wing/room/hall and the full body. Use this when a previous "
    "list_memories or search_memory result showed is_drawer=true and you "
    "need the actual contents."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "drawer_id": {"type": "string", "description": "The drawer key from list/search results"},
    },
    "required": ["drawer_id"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.get_drawer
