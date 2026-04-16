"""Tool: list_recent_files — files sorted by mtime, for 'what was I working on'."""
from __future__ import annotations
import json
import os
import time

NAME = "list_recent_files"
DESCRIPTION = (
    "List files modified most recently, sorted newest-first. Use at session "
    "start for 'what was I working on' when resuming work, or to find the "
    "freshest genus.log* / innovus.log* after a run completes. Walks "
    "recursively up to max_depth and skips the usual noise (.git, "
    "__pycache__, node_modules). Caps results at 50 by default."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to scan (default: current directory)",
        },
        "max_age_hours": {
            "type": "number",
            "description": "Only include files modified within this many hours (default: no limit)",
        },
        "limit": {
            "type": "integer",
            "description": "Max number of files to return (default: 50, max: 200)",
        },
        "max_depth": {
            "type": "integer",
            "description": "Max directory recursion depth (default: 4)",
        },
        "include_pattern": {
            "type": "string",
            "description": "Filename glob to filter (e.g. '*.log', 'genus*')",
        },
    },
}

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_DEFAULT_DEPTH = 4


def handler(
    path: str = ".",
    max_age_hours: float = 0,
    limit: int = _DEFAULT_LIMIT,
    max_depth: int = _DEFAULT_DEPTH,
    include_pattern: str = "",
) -> str:
    import fnmatch

    path = os.path.expanduser(path)
    limit = max(1, min(limit, _MAX_LIMIT))
    max_depth = max(1, min(max_depth, 10))
    cutoff = time.time() - max_age_hours * 3600 if max_age_hours > 0 else None

    root_depth = path.rstrip(os.sep).count(os.sep)
    collected = []

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            current_depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
            if current_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fname in filenames:
                if include_pattern and not fnmatch.fnmatch(fname, include_pattern):
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if cutoff is not None and st.st_mtime < cutoff:
                    continue
                collected.append((st.st_mtime, full, st.st_size))
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})

    # Newest first
    collected.sort(reverse=True)
    truncated = len(collected) > limit
    collected = collected[:limit]

    return json.dumps({
        "ok": True,
        "path": path,
        "count": len(collected),
        "truncated": truncated,
        "files": [
            {
                "path": p,
                "mtime": int(mtime),
                "age_seconds": int(time.time() - mtime),
                "size_bytes": size,
            }
            for mtime, p, size in collected
        ],
    })
