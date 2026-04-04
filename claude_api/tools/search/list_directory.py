"""Tool: list_directory — list directory contents with types and sizes."""
from __future__ import annotations
import json
import os

NAME = "list_directory"
DESCRIPTION = "List directory contents with file types and sizes."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
    },
}


def handler(path: str = ".") -> str:
    path = os.path.expanduser(path)
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            entry = {"name": name, "type": "dir" if os.path.isdir(full) else "file"}
            if entry["type"] == "file":
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    pass
            entries.append(entry)
        return json.dumps({"path": path, "count": len(entries), "entries": entries})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
