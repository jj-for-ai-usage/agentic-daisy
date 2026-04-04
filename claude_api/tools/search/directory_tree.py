"""Tool: directory_tree — recursive directory tree with depth limit."""
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


def handler(path: str = ".", max_depth: int = 3) -> str:
    path = os.path.expanduser(path)
    max_depth = max(1, min(max_depth, MAX_TREE_DEPTH))

    def _walk(current, depth):
        if depth > max_depth:
            return []
        try:
            items = []
            for name in sorted(os.listdir(current)):
                if name.startswith(".") or name in ("__pycache__", "node_modules"):
                    continue
                full = os.path.join(current, name)
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
        return json.dumps({"path": path, "max_depth": max_depth, "tree": _walk(path, 1)})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
