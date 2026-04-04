"""Tool: write_file — write content to a file."""
from __future__ import annotations
import json
import os

NAME = "write_file"
DESCRIPTION = "Write content to a file. Creates parent directories if needed. Overwrites existing content."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to write to"},
        "content": {"type": "string", "description": "Content to write to the file"},
    },
    "required": ["path", "content"],
}


def handler(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return json.dumps({"status": "written", "path": path, "bytes": len(content)})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
