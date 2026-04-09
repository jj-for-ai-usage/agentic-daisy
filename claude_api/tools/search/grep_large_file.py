"""Tool: grep_large_file -- single-file streaming regex search.

Complements search_files, which is recursive and silently skips files
larger than 1 MB. This tool:
  - targets a single file (no recursion)
  - has no size cap; streams line-by-line
  - supports asymmetric before/after context
  - supports pagination via offset (skip first N matches)
  - supports invert (non-matching lines)
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Deque, List

NAME = "grep_large_file"
DESCRIPTION = (
    "Stream-grep a single file for a regex pattern. No size cap. Supports "
    "asymmetric context (before/after), pagination (offset+max_matches), and "
    "invert mode (non-matching lines). Use this instead of search_files for "
    "any single file larger than 1 MB (search_files silently skips those). "
    "If you want a fixed section between two markers rather than all matches, "
    "use extract_section instead."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to search (no recursion)."},
        "pattern": {"type": "string", "description": "Regex pattern."},
        "before": {
            "type": "integer",
            "description": "Lines of context before each match. Default 0.",
        },
        "after": {
            "type": "integer",
            "description": "Lines of context after each match. Default 0.",
        },
        "max_matches": {
            "type": "integer",
            "description": "Max matches to return. Default 100.",
        },
        "offset": {
            "type": "integer",
            "description": "Skip this many matches before returning (for pagination). Default 0.",
        },
        "invert": {
            "type": "boolean",
            "description": "Return non-matching lines instead. Default false.",
        },
    },
    "required": ["path", "pattern"],
}

# Quick binary-file detection: NUL byte in first 4 KB
_BINARY_SNIFF_BYTES = 4096


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(_BINARY_SNIFF_BYTES)
        return b"\x00" in chunk
    except OSError:
        return False


def handler(
    path: str,
    pattern: str,
    before: int = 0,
    after: int = 0,
    max_matches: int = 100,
    offset: int = 0,
    invert: bool = False,
) -> str:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return json.dumps({"error": "not a file or not found", "path": path})

    if _is_binary(path):
        return json.dumps({
            "error": "binary file; refusing to grep",
            "path": path,
        })

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"error": "invalid regex: %s" % exc, "pattern": pattern})

    # Clamp to sensible values
    before = max(0, before)
    after = max(0, after)
    max_matches = max(1, max_matches)
    offset = max(0, offset)

    before_buffer: Deque = deque(maxlen=before) if before > 0 else deque()
    results: List[dict] = []
    total_matched = 0
    pending_after: List[dict] = []  # matches awaiting their trailing context
    after_remaining = 0  # lines of 'after' still owed to the most recent match

    try:
        with open(path, "r", errors="replace") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.rstrip("\n")
                is_match = bool(regex.search(line))
                if invert:
                    is_match = not is_match

                # Top up any pending 'after' context from a previous match
                if after_remaining > 0 and pending_after:
                    pending_after[-1]["context_after"].append(line)
                    after_remaining -= 1

                if is_match:
                    total_matched += 1
                    if total_matched <= offset:
                        # Still in the skip window; advance the before buffer and move on
                        if before > 0:
                            before_buffer.append(line)
                        continue
                    if len(results) >= max_matches:
                        # Hit the cap; don't record more but keep counting total
                        if before > 0:
                            before_buffer.append(line)
                        continue
                    match = {
                        "line_number": lineno,
                        "line": line,
                        "context_before": list(before_buffer) if before > 0 else [],
                        "context_after": [],
                    }
                    results.append(match)
                    pending_after.append(match)
                    after_remaining = after
                else:
                    if after_remaining == 0 and pending_after:
                        # The last match's after-window is satisfied; release it
                        pending_after.pop()

                if before > 0:
                    before_buffer.append(line)
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})

    truncated = total_matched > (offset + len(results))
    return json.dumps({
        "path": path,
        "pattern": pattern,
        "total_matched": total_matched,
        "returned": len(results),
        "offset": offset,
        "truncated": truncated,
        "results": results,
    })
