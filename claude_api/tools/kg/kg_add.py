"""Tool: kg_add -- add a temporal knowledge-graph triple."""
NAME = "kg_add"
DESCRIPTION = (
    "Add a knowledge-graph triple: subject -> predicate -> object, with "
    "optional temporal validity window. Idempotent: an identical "
    "currently-valid triple is not duplicated. Use for durable, "
    "structured facts (e.g. constraint_set_v1 supersedes constraint_set_v0)."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Entity name on the left side."},
        "predicate": {"type": "string",
                      "description": "Relationship verb (normalized to snake_case)."},
        "object_": {"type": "string",
                    "description": "Entity name on the right side."},
        "valid_from": {"type": "string",
                       "description": "ISO date when this fact becomes true."},
        "valid_to": {"type": "string",
                     "description": "ISO date when this fact stops being true. Leave empty for 'currently true'."},
        "confidence": {"type": "number",
                       "description": "0.0-1.0 confidence score. Default 1.0."},
        "source": {"type": "string",
                   "description": "Optional provenance label (memory key, drawer id, ...)."},
        "source_file": {"type": "string",
                        "description": "Optional source file path."},
    },
    "required": ["subject", "predicate", "object_"],
}


def make_handler(kg_store=None, **kwargs):
    return kg_store.add_triple
