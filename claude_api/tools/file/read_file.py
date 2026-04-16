"""Tool: read_file — read file contents."""
from __future__ import annotations
import json
import os

NAME = "read_file"
DESCRIPTION = (
    "Read a file's full contents (truncates silently above 100K chars). "
    "Before calling on a file of unknown size, consider stat_file first — "
    "cheap. Prefer run_command with tail/head/sed -n/grep when you only "
    "need a portion of a large file: a 10-line tail is ~100 tokens vs ~25K "
    "tokens for a 100KB read. Use read_file when you genuinely need a "
    "small file (<5KB) and no narrower extraction is possible."
)
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
            "ok": True, "path": path, "content": content,
            "truncated": truncated, "size_bytes": size,
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})
