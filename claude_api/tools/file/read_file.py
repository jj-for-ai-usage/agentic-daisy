"""Tool: read_file — read file contents."""
from __future__ import annotations
import json
import os

NAME = "read_file"
DESCRIPTION = "Read the contents of a file. Returns up to 100K characters."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Absolute or relative file path to read"},
    },
    "required": ["path"],
}

MAX_READ_SIZE = 100_000


def handler(path: str) -> str:
    path = os.path.expanduser(path)
    try:
        size = os.path.getsize(path)
        with open(path, "r") as f:
            content = f.read(MAX_READ_SIZE + 1)
        truncated = len(content) > MAX_READ_SIZE
        if truncated:
            content = content[:MAX_READ_SIZE]
        return json.dumps({
            "path": path, "content": content,
            "truncated": truncated, "size_bytes": size,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
