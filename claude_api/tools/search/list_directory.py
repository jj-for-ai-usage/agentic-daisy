"""Tool: list_directory — list directory contents with types and sizes."""
from __future__ import annotations
import json
import os

NAME = "list_directory"
DESCRIPTION = (
    "List a single directory's contents with file types, sizes, and mtimes, "
    "as structured JSON. Use when the next step needs to filter/iterate on "
    "the entries programmatically (e.g. 'for each .log file, check mtime'). "
    "For a one-shot human-readable listing, `run_command('ls -la')` is cheaper. "
    "Capped at 500 entries, sorted by name. Does NOT recurse — use "
    "directory_tree or list_recent_files for multi-level scans."
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
