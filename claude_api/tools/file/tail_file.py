"""Tool: tail_file -- read the last N lines of a file, with resume support.

Two modes:
  - Default: return the last N lines (like `tail -n N`).
  - Resume: if after_byte is provided, return only the bytes appended
    since that offset. The returned 'new_cursor' can be fed into the
    next call's after_byte to poll a running job's log incrementally.
"""
from __future__ import annotations

import json
import os

NAME = "tail_file"
DESCRIPTION = (
    "Read the last N lines of a file (default 100), or resume from a byte "
    "offset returned by a previous tail_file call. The 'new_cursor' in the "
    "response can be passed back as 'after_byte' to poll a growing file "
    "(e.g. a running job's log) without re-reading content you've already seen."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "lines": {
            "type": "integer",
            "description": "Number of lines from the end. Default 100. Ignored if after_byte is set.",
        },
        "after_byte": {
            "type": "integer",
            "description": (
                "If set, return everything appended since this byte offset "
                "(from a previous tail_file call's 'new_cursor'). Default: not set."
            ),
        },
    },
    "required": ["path"],
}

DEFAULT_LINES = 100
MAX_RESPONSE = 100_000
CHUNK = 8192


def _tail_lines(path: str, file_size: int, n: int) -> tuple:
    """Return (content, bytes_covered) for the last n lines."""
    if n <= 0 or file_size == 0:
        return "", 0
    with open(path, "rb") as f:
        # Walk backwards in chunks until we've seen n+1 newlines (so we can
        # skip the partial line at the start of the final chunk).
        pos = file_size
        buf = b""
        newlines_seen = 0
        while pos > 0 and newlines_seen <= n:
            read_size = min(CHUNK, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            buf = chunk + buf
            newlines_seen = buf.count(b"\n")
        # Extract the last n lines
        lines = buf.splitlines(keepends=True)
        if len(lines) > n:
            lines = lines[-n:]
        content_bytes = b"".join(lines)
        content = content_bytes.decode("utf-8", errors="replace")
        return content, len(content_bytes)


def handler(path: str, lines: int = DEFAULT_LINES, after_byte: int = -1) -> str:
    path = os.path.expanduser(path)
    try:
        file_size = os.path.getsize(path)
    except OSError as exc:
        return json.dumps({"error": str(exc), "path": path})

    # Cursor-resume mode: after_byte is explicitly set (>= 0)
    if after_byte >= 0:
        if after_byte > file_size:
            # File was truncated or rotated since the previous call
            return json.dumps({
                "error": "file_truncated",
                "hint": "file_size < after_byte; file was truncated or rotated. Re-tail from the end.",
                "path": path,
                "after_byte": after_byte,
                "file_size": file_size,
            })
        if after_byte == file_size:
            return json.dumps({
                "path": path,
                "content": "",
                "bytes_read": 0,
                "new_cursor": file_size,
                "file_size": file_size,
                "truncated": False,
            })
        available = file_size - after_byte
        read_bytes = min(available, MAX_RESPONSE)
        with open(path, "rb") as f:
            f.seek(after_byte)
            raw = f.read(read_bytes)
        content = raw.decode("utf-8", errors="replace")
        return json.dumps({
            "path": path,
            "content": content,
            "bytes_read": len(raw),
            "new_cursor": after_byte + len(raw),
            "file_size": file_size,
            "truncated": len(raw) < available,
        })

    # Tail-N-lines mode
    lines = max(1, lines)
    try:
        content, content_len = _tail_lines(path, file_size, lines)
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})

    truncated = False
    if len(content) > MAX_RESPONSE:
        # Trim from the *front* so we keep the most recent tail
        content = content[-MAX_RESPONSE:]
        content_len = len(content.encode("utf-8", errors="replace"))
        truncated = True

    new_cursor = file_size
    return json.dumps({
        "path": path,
        "content": content,
        "bytes_read": content_len,
        "new_cursor": new_cursor,
        "file_size": file_size,
        "truncated": truncated,
    })
