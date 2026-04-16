"""Tool: directory_tree — recursive directory tree with depth limit."""
from __future__ import annotations
import json
import os

NAME = "directory_tree"
DESCRIPTION = (
    "Recursive directory tree with a depth limit, useful ONCE at the start "
    "of an exploration to understand overall project layout. Skips hidden "
    "dirs, __pycache__/, node_modules/. Capped at 2000 entries, max depth 5. "
    "For a single directory's contents use list_directory; for recently-"
    "modified files use list_recent_files; for a quick human-readable "
    "listing run_command('ls') is cheaper. Do NOT re-run this tool every "
    "turn — call it once per new project area."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Root directory for the tree (default: current directory)"},
        "max_depth": {"type": "integer", "description": "Maximum depth to recurse (default: 3, max: 5)"},
    },
}

MAX_TREE_DEPTH = 5
MAX_ENTRIES = 2000


def handler(path: str = ".", max_depth: int = 3) -> str:
    path = os.path.expanduser(path)
    max_depth = max(1, min(max_depth, MAX_TREE_DEPTH))

    entry_count = [0]  # mutable counter for nested function

    def _walk(current, depth):
        if depth > max_depth or entry_count[0] >= MAX_ENTRIES:
            return []
        try:
            items = []
            for name in sorted(os.listdir(current)):
                if entry_count[0] >= MAX_ENTRIES:
                    break
                if name.startswith(".") or name in ("__pycache__", "node_modules"):
                    continue
                full = os.path.join(current, name)
                entry_count[0] += 1
                if os.path.isdir(full):
                    items.append({"name": name, "type": "dir", "children": _walk(full, depth + 1)})
                else:
                    entry = {"name": name, "type": "file"}
                    try:
                        entry["size"] = os.path.getsize(full)
                    except OSError:
                        pass
                    items.append(entry)
            return items
        except OSError:
            return []

    try:
        tree = _walk(path, 1)
        result = {
            "ok": True,
            "path": path,
            "max_depth": max_depth,
            "entries": entry_count[0],
            "tree": tree,
            "truncated": False,
        }
        if entry_count[0] >= MAX_ENTRIES:
            result["truncated"] = True
            result["message"] = (
                "Tree capped at %d entries. Use run_command('find ...') "
                "or list_recent_files for deeper exploration." % MAX_ENTRIES
            )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})
