"""Tool: kg_invalidate -- close the validity window of a currently-true triple."""
NAME = "kg_invalidate"
DESCRIPTION = (
    "Mark a knowledge-graph triple as no longer valid by setting its "
    "valid_to date. Never deletes -- the timeline stays intact. Use this "
    "when a fact that was true becomes obsolete (e.g. a constraint set "
    "gets replaced)."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        "object_": {"type": "string"},
        "ended": {"type": "string",
                  "description": "ISO date when the fact stopped being true. Defaults to today."},
    },
    "required": ["subject", "predicate", "object_"],
}


def make_handler(kg_store=None, **kwargs):
    return kg_store.invalidate
