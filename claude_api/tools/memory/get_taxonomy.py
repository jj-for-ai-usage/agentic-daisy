"""Tool: get_taxonomy -- return the full wing/room/hall tree with counts."""
NAME = "get_taxonomy"
DESCRIPTION = (
    "Return the full namespace tree as {wing: {room: {hall: count}}}. "
    "Useful to see everything that's been categorized without reading "
    "individual memories."
)
INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_handler(memory_store=None, **kwargs):
    return memory_store.get_taxonomy
