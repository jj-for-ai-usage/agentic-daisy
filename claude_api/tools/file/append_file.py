"""Tool: append_file — append content to end of a file."""
from __future__ import annotations
import json
import os

NAME = "append_file"
DESCRIPTION = "Append content to the end of a file without reading it first. Creates the file if it doesn't exist."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to append to"},
        "content": {"type": "string", "description": "Content to append"},
    },
    "required": ["path", "content"],
}


def handler(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a") as f:
            f.write(content)
        return json.dumps({"status": "appended", "path": path, "bytes_added": len(content)})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
