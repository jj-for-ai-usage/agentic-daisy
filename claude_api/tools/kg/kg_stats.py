"""Tool: kg_stats -- summary counts for the knowledge graph."""
NAME = "kg_stats"
DESCRIPTION = (
    "Return counts of entities, triples, current/expired facts, and the "
    "list of predicate types used. Useful for sanity-checking the KG."
)
INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_handler(kg_store=None, **kwargs):
    return kg_store.stats
