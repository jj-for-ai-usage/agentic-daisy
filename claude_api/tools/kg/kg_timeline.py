"""Tool: kg_timeline -- chronological view of facts."""
NAME = "kg_timeline"
DESCRIPTION = (
    "Return up to 100 knowledge-graph facts in chronological order. "
    "If entity is passed, only facts touching that entity are returned. "
    "Useful for reconstructing a change history."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity": {
            "type": "string",
            "description": "Optional entity to filter by.",
        },
    },
}


def make_handler(kg_store=None, **kwargs):
    return kg_store.timeline
