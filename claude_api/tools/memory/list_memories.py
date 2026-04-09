"""Tool: list_memories -- list saved memory keys, optionally by namespace."""
from ...memory import HALL_VALUES

NAME = "list_memories"
DESCRIPTION = (
    "List saved memory keys with their tags, namespace, and last-updated "
    "timestamp. Optional wing/room/hall filters narrow the listing."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "wing": {"type": "string", "description": "Filter by wing"},
        "room": {"type": "string", "description": "Filter by room"},
        "hall": {
            "type": "string",
            "enum": list(HALL_VALUES),
            "description": "Filter by hall",
        },
    },
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.list_memories
