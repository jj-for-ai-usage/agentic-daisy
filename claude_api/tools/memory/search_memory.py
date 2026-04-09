"""Tool: search_memory -- find saved memories by keyword, tag, or namespace."""
from ...memory import HALL_VALUES

NAME = "search_memory"
DESCRIPTION = (
    "Search saved memories. Filter by substring (keys and values), tag, "
    "wing/room/hall namespace, and/or a point-in-time (as_of) date. All "
    "filters are optional and stack."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "Substring match on key and value"},
        "tag": {"type": "string", "description": "Filter by exact tag"},
        "wing": {"type": "string", "description": "Filter by wing (chip/design)"},
        "room": {"type": "string", "description": "Filter by room (block)"},
        "hall": {
            "type": "string",
            "enum": list(HALL_VALUES),
            "description": "Filter by hall (category)",
        },
        "as_of": {
            "type": "string",
            "description": (
                "ISO date. Only return memories whose valid_from/valid_until "
                "window contains this date."
            ),
        },
    },
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.search_memory
