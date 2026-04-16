"""Tool: list_directory — list directory contents with types and sizes."""
from __future__ import annotations
import json
import os

NAME = "list_directory"
DESCRIPTION = (
    "List directory contents with file types, sizes, and mtimes. Returns "
    "structured JSON (useful when Claude will iterate on the results). "
    "For a quick human-readable listing, `run_command('ls -la')` is cheaper. "
    "Capped at 500 entries, sorted by name."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
    },
}

MAX_ENTRIES = 500


def handler(path: str = ".") -> str:
    path = os.path.expanduser(path)
    try:
        names = sorted(os.listdir(path))
        total = len(names)
        truncated = total > MAX_ENTRIES
        if truncated:
            names = names[:MAX_ENTRIES]
        entries = []
        for name in names:
            full = os.path.join(path, name)
            entry = {"name": name, "type": "dir" if os.path.isdir(full) else "file"}
            try:
                st = os.stat(full)
                if entry["type"] == "file":
                    entry["size"] = st.st_size
                entry["mtime"] = int(st.st_mtime)
            except OSError:
                pass
            entries.append(entry)
        return json.dumps({
            "ok": True,
            "path": path,
            "count": len(entries),
            "total": total,
            "truncated": truncated,
            "entries": entries,
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})
