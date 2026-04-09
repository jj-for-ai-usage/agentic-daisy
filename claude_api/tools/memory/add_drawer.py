"""Tool: add_drawer -- archive verbatim content under a wing/room."""
NAME = "add_drawer"
DESCRIPTION = (
    "Archive a verbatim blob (log excerpt, report, etc.) under a "
    "wing/room namespace. The full content is stored in a drawer file; "
    "only the pointer + metadata is indexed in memory. Use this for "
    "large tool outputs you want to keep verbatim. Either pass 'content' "
    "inline, or pass 'spill_file' to ingest an existing file (typically "
    "a path returned by a tool that spilled to disk). Duplicates are "
    "detected by SHA256 of the body."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "wing": {"type": "string",
                 "description": "Chip/design namespace (required)."},
        "room": {"type": "string",
                 "description": "Block namespace (required)."},
        "content": {"type": "string",
                    "description": "Inline content to archive. Pass this OR spill_file."},
        "spill_file": {
            "type": "string",
            "description": (
                "Path to an existing file to ingest. Typically a "
                "spill path returned by another tool."
            ),
        },
        "hall": {
            "type": "string",
            "description": "Optional category (timing, power, drc, ...).",
        },
        "source_file": {
            "type": "string",
            "description": "Optional source path this content came from.",
        },
        "added_by": {
            "type": "string",
            "description": "Who/what added this drawer. Default 'agent'.",
        },
        "importance": {
            "type": "integer",
            "description": "0-100 importance score.",
        },
    },
    "required": ["wing", "room"],
}


def make_handler(memory_store=None, **kwargs):
    return memory_store.add_drawer
