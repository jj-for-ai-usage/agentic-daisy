"""Agentic Daisy — File read/write/list tools."""
from __future__ import annotations

import json
import os
from typing import Optional

MAX_READ_SIZE = 100_000  # characters


def read_file(path: str) -> str:
    """Read and return file contents. Truncates at MAX_READ_SIZE."""
    path = os.path.expanduser(path)
    try:
        size = os.path.getsize(path)
        with open(path, "r") as f:
            content = f.read(MAX_READ_SIZE + 1)
        truncated = len(content) > MAX_READ_SIZE
        if truncated:
            content = content[:MAX_READ_SIZE]
        return json.dumps({
            "path": path,
            "content": content,
            "truncated": truncated,
            "size_bytes": size,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    path = os.path.expanduser(path)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return json.dumps({
            "status": "written",
            "path": path,
            "bytes": len(content),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def list_directory(path: str = ".") -> str:
    """List directory contents with type and size info."""
    path = os.path.expanduser(path)
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            entry = {
                "name": name,
                "type": "dir" if os.path.isdir(full) else "file",
            }
            if entry["type"] == "file":
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    pass
            entries.append(entry)
        return json.dumps({
            "path": path,
            "count": len(entries),
            "entries": entries,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
