"""Tool: extract_section -- carve out a slice of a file by markers.

Two modes:
  - 'between': return the lines between the first match of start_pattern
    and the next match of end_pattern (inclusive of the start match;
    exclusive of the next end match).
  - 'around':  return 'before' lines before and 'after' lines after an
    anchor match. Use to pull a window around an error or event.
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Deque, List

NAME = "extract_section"
DESCRIPTION = (
    "Extract a single contiguous section of a file by pattern. Mode 'between' "
    "returns lines from the first start_pattern match through the next "
    "end_pattern match (use 'occurrence' to pick the Nth start). Mode "
    "'around' returns a window of 'before' lines + anchor_pattern match + "
    "'after' lines. Streaming, works on huge files. "
    "Use this (not grep_large_file) when you want ONE section, not a list "
    "of matches."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "mode": {
            "type": "string",
            "enum": ["between", "around"],
            "description": (
                "'between' uses start_pattern + end_pattern. "
                "'around' uses anchor_pattern + before/after line counts."
            ),
        },
        "start_pattern": {
            "type": "string",
            "description": "Regex marking the start of the section (between mode).",
        },
        "end_pattern": {
            "type": "string",
            "description": "Regex marking the end of the section (between mode).",
        },
        "anchor_pattern": {
            "type": "string",
            "description": "Regex marking the anchor line (around mode).",
        },
        "before": {
            "type": "integer",
            "description": "Lines to include before the anchor (around mode). Default 10.",
        },
        "after": {
            "type": "integer",
            "description": "Lines to include after the anchor (around mode). Default 40.",
        },
        "occurrence": {
            "type": "integer",
            "description": "Which match to use (1-indexed). Default 1.",
        },
        "max_lines": {
            "type": "integer",
            "description": "Cap the returned section size. Default 500.",
        },
    },
    "required": ["path", "mode"],
}

DEFAULT_BEFORE = 10
DEFAULT_AFTER = 40
DEFAULT_MAX_LINES = 500
MAX_RESPONSE = 100_000


def _render(lines: List[str], start_line: int, end_line: int, mode: str, path: str) -> str:
    content = "\n".join(lines)
    truncated = False
    if len(content) > MAX_RESPONSE:
        content = content[:MAX_RESPONSE]
        truncated = True
    return json.dumps({
        "path": path,
        "mode": mode,
        "start_line": start_line,
        "end_line": end_line,
        "lines_returned": len(lines),
        "content": content,
        "truncated": truncated,
    })


def handler(
    path: str,
    mode: str,
    start_pattern: str = "",
    end_pattern: str = "",
    anchor_pattern: str = "",
    before: int = DEFAULT_BEFORE,
    after: int = DEFAULT_AFTER,
    occurrence: int = 1,
    max_lines: int = DEFAULT_MAX_LINES,
) -> str:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return json.dumps({"error": "not a file or not found", "path": path})

    occurrence = max(1, occurrence)
    max_lines = max(1, max_lines)
    before = max(0, before)
    after = max(0, after)

    if mode == "between":
        if not start_pattern or not end_pattern:
            return json.dumps({
                "error": "between mode requires start_pattern and end_pattern",
                "path": path,
            })
        try:
            start_rx = re.compile(start_pattern)
            end_rx = re.compile(end_pattern)
        except re.error as exc:
            return json.dumps({"error": "invalid regex: %s" % exc})

        found_starts = 0
        in_section = False
        section_lines: List[str] = []
        start_line = -1
        end_line = -1
        try:
            with open(path, "r", errors="replace") as f:
                for lineno, raw in enumerate(f, start=1):
                    line = raw.rstrip("\n")
                    if not in_section:
                        if start_rx.search(line):
                            found_starts += 1
                            if found_starts == occurrence:
                                in_section = True
                                start_line = lineno
                                section_lines.append(line)
                        continue
                    # In-section: collect until end_pattern or cap
                    if end_rx.search(line):
                        end_line = lineno
                        break
                    section_lines.append(line)
                    if len(section_lines) >= max_lines:
                        end_line = lineno
                        break
        except Exception as exc:
            return json.dumps({"error": str(exc), "path": path})

        if start_line == -1:
            return json.dumps({
                "error": "start_pattern not found (occurrence=%d)" % occurrence,
                "path": path,
            })
        if end_line == -1:
            end_line = start_line + len(section_lines) - 1
        return _render(section_lines, start_line, end_line, "between", path)

    elif mode == "around":
        if not anchor_pattern:
            return json.dumps({
                "error": "around mode requires anchor_pattern",
                "path": path,
            })
        try:
            anchor_rx = re.compile(anchor_pattern)
        except re.error as exc:
            return json.dumps({"error": "invalid anchor_pattern: %s" % exc})

        before_buf: Deque = deque(maxlen=before) if before > 0 else deque()
        found = 0
        anchor_line = -1
        section_lines: List[str] = []
        after_remaining = 0
        try:
            with open(path, "r", errors="replace") as f:
                for lineno, raw in enumerate(f, start=1):
                    line = raw.rstrip("\n")
                    if anchor_line == -1:
                        if anchor_rx.search(line):
                            found += 1
                            if found == occurrence:
                                anchor_line = lineno
                                section_lines.extend(before_buf)
                                section_lines.append(line)
                                after_remaining = after
                                if after_remaining == 0:
                                    break
                                continue
                        if before > 0:
                            before_buf.append(line)
                    else:
                        section_lines.append(line)
                        after_remaining -= 1
                        if after_remaining <= 0 or len(section_lines) >= max_lines:
                            break
        except Exception as exc:
            return json.dumps({"error": str(exc), "path": path})

        if anchor_line == -1:
            return json.dumps({
                "error": "anchor_pattern not found (occurrence=%d)" % occurrence,
                "path": path,
            })
        start_line = max(1, anchor_line - before)
        end_line = start_line + len(section_lines) - 1
        return _render(section_lines, start_line, end_line, "around", path)

    else:
        return json.dumps({"error": "mode must be 'between' or 'around'", "path": path})
