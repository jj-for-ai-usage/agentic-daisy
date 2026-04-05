"""Tool: find_files — find files by glob pattern."""
from __future__ import annotations
import fnmatch
import json
import os
from typing import List

NAME = "find_files"
DESCRIPTION = "Find files by name using a glob pattern. Returns matching file paths. Recursively walks directories."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Glob pattern to match filenames (e.g. '*.py', '*.log')"},
        "path": {"type": "string", "description": "Directory to search in (default: current directory)"},
    },
    "required": ["pattern"],
}

MAX_FIND_RESULTS = 500


def handler(pattern: str, path: str = ".") -> str:
    path = os.path.expanduser(path)
    results = []  # type: List[str]
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fname in sorted(filenames):
                if fnmatch.fnmatch(fname, pattern):
                    results.append(os.path.join(dirpath, fname))
                    if len(results) >= MAX_FIND_RESULTS:
                        return json.dumps({"pattern": pattern, "path": path,
                                           "count": len(results), "truncated": True, "files": results})
        return json.dumps({"pattern": pattern, "path": path, "count": len(results),
                           "truncated": False, "files": results})
    except Exception as exc:
        return json.dumps({"error": str(exc), "pattern": pattern, "path": path})
