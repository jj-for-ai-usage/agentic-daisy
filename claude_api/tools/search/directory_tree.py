"""Tool: directory_tree -- recursive directory tree with depth limit."""
from __future__ import annotations
import json
import os

NAME = "directory_tree"
DESCRIPTION = "Show a recursive directory tree with depth limit. Good for understanding project structure."
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
            "path": path,
            "max_depth": max_depth,
            "entries": entry_count[0],
            "tree": tree,
        }
        if entry_count[0] >= MAX_ENTRIES:
            result["truncated"] = True
            result["message"] = (
                "Tree capped at %d entries. Use find_files or "
                "run_command('find ...') for deeper exploration." % MAX_ENTRIES
            )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
