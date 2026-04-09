"""Tool: read_slice -- read an arbitrary line or byte range from a file.

Complements read_file (which only reads from the start and caps at 100K).
Use this to navigate huge files without loading them entirely into context.
"""
from __future__ import annotations

import json
import os
from itertools import islice

NAME = "read_slice"
DESCRIPTION = (
    "Read an arbitrary range from a file by line numbers or byte offsets. "
    "For 'lines' mode, start/end are 1-indexed and inclusive. "
    "For 'bytes' mode, start/end are 0-indexed and inclusive. "
    "Response is capped at 100K characters; the 'truncated' flag indicates "
    "whether the slice was cut short."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to read."},
        "mode": {
            "type": "string",
            "enum": ["lines", "bytes"],
            "description": "Slice by line numbers ('lines') or byte offsets ('bytes').",
        },
        "start": {
            "type": "integer",
            "description": "Inclusive start: 1-indexed line number, or 0-indexed byte offset.",
        },
        "end": {
            "type": "integer",
            "description": "Inclusive end: 1-indexed line number, or 0-indexed byte offset.",
        },
    },
    "required": ["path", "mode", "start", "end"],
}

MAX_RESPONSE = 100_000


def handler(path: str, mode: str, start: int, end: int) -> str:
    path = os.path.expanduser(path)
    try:
        file_size = os.path.getsize(path)
    except OSError as exc:
        return json.dumps({"error": str(exc), "path": path})

    if end < start:
        return json.dumps({
            "error": "end (%d) must be >= start (%d)" % (end, start),
            "path": path,
        })

    try:
        if mode == "lines":
            if start < 1:
                return json.dumps({"error": "lines mode: start must be >= 1", "path": path})
            # islice is 0-indexed, exclusive end. Convert 1-indexed inclusive
            # [start, end] to islice(start-1, end).
            content_parts = []
            total_len = 0
            truncated = False
            with open(path, "r", errors="replace") as f:
                for line in islice(f, start - 1, end):
                    if total_len + len(line) > MAX_RESPONSE:
                        content_parts.append(line[: MAX_RESPONSE - total_len])
                        truncated = True
                        break
                    content_parts.append(line)
                    total_len += len(line)
            content = "".join(content_parts)
            return json.dumps({
                "path": path,
                "mode": "lines",
                "start": start,
                "end": end,
                "content": content,
                "total_bytes": file_size,
                "truncated": truncated,
            })

        elif mode == "bytes":
            if start < 0:
                return json.dumps({"error": "bytes mode: start must be >= 0", "path": path})
            if start >= file_size:
                return json.dumps({
                    "path": path, "mode": "bytes", "start": start, "end": end,
                    "content": "", "total_bytes": file_size, "truncated": False,
                })
            requested = end - start + 1
            capped = min(requested, MAX_RESPONSE)
            with open(path, "rb") as f:
                f.seek(start)
                raw = f.read(capped)
            content = raw.decode("utf-8", errors="replace")
            truncated = requested > MAX_RESPONSE or (start + requested) > file_size
            return json.dumps({
                "path": path,
                "mode": "bytes",
                "start": start,
                "end": end,
                "content": content,
                "total_bytes": file_size,
                "truncated": truncated,
            })

        else:
            return json.dumps({"error": "mode must be 'lines' or 'bytes'", "path": path})

    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
