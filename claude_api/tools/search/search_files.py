"""Tool: search_files — regex content search across files."""
from __future__ import annotations
import fnmatch
import json
import os
import re
from typing import List

NAME = "search_files"
DESCRIPTION = (
    "Search file contents by regex pattern. Returns matching lines "
    "with file paths and line numbers. Walks directories recursively."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regex pattern to search for in file contents"},
        "path": {"type": "string", "description": "Directory to search in (default: current directory)"},
        "include": {"type": "string", "description": "Glob pattern to filter filenames (e.g. '*.py', '*.conf')"},
        "context_lines": {"type": "integer", "description": "Number of lines of context around each match (default: 0)"},
    },
    "required": ["pattern"],
}

MAX_SEARCH_RESULTS = 200


def handler(pattern: str, path: str = ".", include: str = "", context_lines: int = 0) -> str:
    path = os.path.expanduser(path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"error": "Invalid regex: %s" % exc, "pattern": pattern})

    results = []  # type: List[dict]
    files_searched = 0

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fname in sorted(filenames):
                if include and not fnmatch.fnmatch(fname, include):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    if os.path.getsize(fpath) > 1_000_000:
                        continue
                except OSError:
                    continue
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        file_lines = f.readlines()
                except (OSError, UnicodeDecodeError):
                    continue
                files_searched += 1
                for i, line in enumerate(file_lines):
                    if regex.search(line):
                        match = {"file": fpath, "line_number": i + 1, "line": line.rstrip("\n")}
                        if context_lines > 0:
                            start = max(0, i - context_lines)
                            end = min(len(file_lines), i + context_lines + 1)
                            match["context"] = [ln.rstrip("\n") for ln in file_lines[start:end]]
                        results.append(match)
                        if len(results) >= MAX_SEARCH_RESULTS:
                            return json.dumps({"pattern": pattern, "matches": len(results),
                                               "truncated": True, "files_searched": files_searched,
                                               "results": results})

        return json.dumps({"pattern": pattern, "matches": len(results), "truncated": False,
                           "files_searched": files_searched, "results": results})
    except Exception as exc:
        return json.dumps({"error": str(exc), "pattern": pattern, "path": path})
