"""Tool: traverse -- BFS over the palace graph from a starting room."""
from claude_api import palace_graph

NAME = "traverse"
DESCRIPTION = (
    "Walk the palace graph from a starting room. Returns rooms reachable "
    "via shared wings within max_hops. Useful for discovering related "
    "blocks that live in the same chip(s)."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "start_room": {"type": "string", "description": "Room (block) to start from."},
        "max_hops": {"type": "integer",
                     "description": "Max BFS depth. Default 2."},
    },
    "required": ["start_room"],
}


def make_handler(memory_store=None, **kwargs):
    def _handler(start_room, max_hops=2):
        return palace_graph.traverse(memory_store, start_room, max_hops)
    return _handler
