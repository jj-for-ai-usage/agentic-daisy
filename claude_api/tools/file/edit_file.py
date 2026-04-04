"""Tool: edit_file — surgical find-and-replace in a file."""
from __future__ import annotations
import json
import os

NAME = "edit_file"
DESCRIPTION = (
    "Surgical find-and-replace in a file. Only changes the matched "
    "text — much cheaper than rewriting the whole file with write_file. "
    "The old_string must match exactly once."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to edit"},
        "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
        "new_string": {"type": "string", "description": "Replacement text"},
    },
    "required": ["path", "old_string", "new_string"],
}


def handler(path: str, old_string: str, new_string: str) -> str:
    path = os.path.expanduser(path)
    try:
        with open(path, "r") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return json.dumps({"error": "old_string not found in file", "path": path})
        if count > 1:
            return json.dumps({
                "error": "old_string matches %d times — provide more context to make it unique" % count,
                "path": path, "matches": count,
            })
        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        return json.dumps({"status": "edited", "path": path, "replacements": 1})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
