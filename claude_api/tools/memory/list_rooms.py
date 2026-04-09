"""Tool: list_rooms -- count memories per room, optionally scoped to a wing."""
NAME = "list_rooms"
DESCRIPTION = (
    "List rooms (block names) with a memory count for each. Pass a wing "
    "to scope the listing to one chip/design."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "wing": {
            "type": "string",
            "description": "Optional wing to filter by (chip/design).",
        },
    },
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.list_rooms
