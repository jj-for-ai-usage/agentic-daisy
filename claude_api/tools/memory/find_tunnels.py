"""Tool: find_tunnels -- rooms shared across multiple wings."""
from claude_api import palace_graph

NAME = "find_tunnels"
DESCRIPTION = (
    "Return rooms (blocks) that appear in two or more wings (chips). "
    "Passing wing_a and/or wing_b narrows the search. A 'tunnel' is "
    "conceptually a shared idea/block across designs -- for EDA that's "
    "typically a reused IP block or a common constraint file."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "wing_a": {"type": "string", "description": "Optional wing filter."},
        "wing_b": {"type": "string", "description": "Optional second wing filter."},
    },
}


def make_handler(memory_store=None, **kwargs):
    def _handler(wing_a="", wing_b=""):
        return palace_graph.find_tunnels(memory_store, wing_a, wing_b)
    return _handler
