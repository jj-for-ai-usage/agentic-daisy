"""Tool: kg_query -- query the knowledge graph for an entity's triples."""
NAME = "kg_query"
DESCRIPTION = (
    "Return triples touching an entity. direction='outgoing' (default) gives "
    "facts the entity is the subject of; 'incoming' gives facts where it's "
    "the object; 'both' merges. Pass as_of to see a historical snapshot."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Entity name to query."},
        "as_of": {
            "type": "string",
            "description": "ISO date. Only return facts valid at this point in time.",
        },
        "direction": {
            "type": "string",
            "enum": ["outgoing", "incoming", "both"],
            "description": "Which side of the relation the entity is on.",
        },
    },
    "required": ["name"],
}


def make_handler(kg_store=None, **kwargs):
    return kg_store.query_entity
