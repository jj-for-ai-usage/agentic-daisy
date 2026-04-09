"""Tool: save_memory -- persist information across conversations."""
from ...memory import HALL_VALUES

NAME = "save_memory"
DESCRIPTION = (
    "Save a piece of information for later retrieval. "
    "Use this to remember important context, decisions, or facts "
    "across conversations. Optional wing/room/hall fields let you "
    "namespace a memory to a specific chip/block/flow-stage."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Short identifier for this memory (e.g. 'project-status')",
        },
        "value": {"type": "string", "description": "The content to remember"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional categorization tags",
        },
        "wing": {
            "type": "string",
            "description": "Optional namespace: chip/design name (e.g. 'chipA').",
        },
        "room": {
            "type": "string",
            "description": "Optional sub-namespace: block name (e.g. 'cpu_core').",
        },
        "hall": {
            "type": "string",
            "enum": list(HALL_VALUES),
            "description": (
                "Optional category within the room. Must be one of: "
                "timing, power, drc, floorplan, cts, synth, constraint, "
                "workaround, facts."
            ),
        },
        "source_file": {
            "type": "string",
            "description": "Optional path of the source this memory came from.",
        },
        "valid_from": {
            "type": "string",
            "description": "Optional ISO date when this fact starts being true.",
        },
        "valid_until": {
            "type": "string",
            "description": "Optional ISO date when this fact stops being true.",
        },
        "importance": {
            "type": "integer",
            "description": "0-100 importance score. Top-N by importance are loaded at session start.",
        },
    },
    "required": ["key", "value"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.save_memory
