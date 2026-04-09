"""Tool: kg_timeline -- chronological view of facts."""
NAME = "kg_timeline"
DESCRIPTION = (
    "Return up to 100 knowledge-graph facts in chronological order. "
    "If entity is passed, only facts touching that entity are returned. "
    "If as_of is passed, only facts established on or before that date "
    "are returned. Useful for reconstructing a change history."
)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entity": {
            "type": "string",
            "description": "Optional entity to filter by.",
        },
        "as_of": {
            "type": "string",
            "description": "Optional ISO date -- return the timeline as it "
                           "would have looked on that date.",
        },
    },
}


def make_handler(kg_store=None, **kwargs):
    return kg_store.timeline
