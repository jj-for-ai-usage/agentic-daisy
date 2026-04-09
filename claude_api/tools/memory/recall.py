"""Tool: recall -- L2 on-demand retrieval by wing/room/hall."""
from claude_api.memory_stack import Layer2

NAME = "recall"
DESCRIPTION = (
    "Pull back memories scoped to a wing/room/hall. This is the L2 layer "
    "of the memory stack: it returns a compact listing of records whose "
    "namespace matches. Cheaper than save_memory + search_memory round "
    "trips when you already know which chip/block/category you want."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "wing": {"type": "string"},
        "room": {"type": "string"},
        "hall": {"type": "string"},
        "n_results": {"type": "integer", "description": "Max results, default 10."},
    },
}


def make_handler(memory_store=None, **kwargs):
    layer2 = Layer2(memory_store)
    def _handler(wing="", room="", hall="", n_results=10):
        return layer2.retrieve(wing=wing, room=room, hall=hall,
                               n_results=n_results)
    return _handler
