"""Tool: list_wings -- count memories per wing (chip/design namespace)."""
NAME = "list_wings"
DESCRIPTION = (
    "List all wings (chip/design namespaces) with the count of memories "
    "under each. Useful for discovering what projects have stored context."
)
INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_handler(memory_store=None, **kwargs):
    return memory_store.list_wings
