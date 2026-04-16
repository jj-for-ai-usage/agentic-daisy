"""Tool: find_files — find files by glob pattern."""
from __future__ import annotations
import fnmatch
import json
import os
from typing import List

NAME = "find_files"
DESCRIPTION = (
    "Find files by name using a glob pattern; recursively walks directories. "
    "Skips .git/, __pycache__/, node_modules/, and hidden directories. "
    "For a one-off `find` invocation, `run_command('find ...')` is fine; "
    "prefer find_files when you'll iterate on the result list programmatically."
)
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
                        return json.dumps({
                            "ok": True, "pattern": pattern, "path": path,
                            "count": len(results), "truncated": True,
                            "files": results,
                        })
        return json.dumps({
            "ok": True, "pattern": pattern, "path": path,
            "count": len(results), "truncated": False, "files": results,
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "pattern": pattern, "path": path})
