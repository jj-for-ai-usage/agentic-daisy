"""Tool: diff_files — compare two files and return a unified diff."""
from __future__ import annotations
import difflib
import json
import os

NAME = "diff_files"
DESCRIPTION = (
    "Produce a unified diff between two files. Much cheaper than reading "
    "both files into context and comparing mentally — a diff of two 100KB "
    "files is typically ~1-5KB of output. Ideal for: comparing QoR across "
    "two runs, reviewing SDC/Tcl changes before applying, checking what "
    "changed in a log between checkpoints. Output is capped at 50K chars; "
    "larger diffs are truncated with a note."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path_a": {"type": "string", "description": "First file (the 'before')"},
        "path_b": {"type": "string", "description": "Second file (the 'after')"},
        "context_lines": {
            "type": "integer",
            "description": "Unified diff context lines around each change (default: 3)",
        },
    },
    "required": ["path_a", "path_b"],
}

_MAX_OUTPUT = 50_000
_MAX_FILE_SIZE = 5_000_000  # 5MB per side; larger and diff becomes useless


def handler(path_a: str, path_b: str, context_lines: int = 3) -> str:
    pa = os.path.expanduser(path_a)
    pb = os.path.expanduser(path_b)
    for p in (pa, pb):
        if not os.path.exists(p):
            return json.dumps({"ok": False, "error": "File not found", "path": p})
        if os.path.isdir(p):
            return json.dumps({
                "ok": False,
                "error": "Path is a directory (diff_files compares single files)",
                "path": p,
            })
        if os.path.getsize(p) > _MAX_FILE_SIZE:
            return json.dumps({
                "ok": False,
                "error": "File exceeds 5MB diff limit — preprocess with run_python to extract the region of interest",
                "path": p,
                "size_bytes": os.path.getsize(p),
            })
    try:
        with open(pa, "r", errors="replace") as f:
            lines_a = f.readlines()
        with open(pb, "r", errors="replace") as f:
            lines_b = f.readlines()
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)})

    if lines_a == lines_b:
        return json.dumps({
            "ok": True, "identical": True, "path_a": pa, "path_b": pb,
            "diff": "",
            "changed_lines": 0,
        })

    diff_lines = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=pa, tofile=pb,
        n=max(0, min(context_lines, 20)),
    ))
    diff_text = "".join(diff_lines)
    truncated = False
    if len(diff_text) > _MAX_OUTPUT:
        diff_text = diff_text[:_MAX_OUTPUT] + "\n[... diff truncated at %d chars ...]" % _MAX_OUTPUT
        truncated = True

    # Count actual add/remove lines (exclude hunk headers + file headers)
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    return json.dumps({
        "ok": True,
        "identical": False,
        "path_a": pa,
        "path_b": pb,
        "lines_added": added,
        "lines_removed": removed,
        "truncated": truncated,
        "diff": diff_text,
    })
