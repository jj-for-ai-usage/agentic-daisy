"""Tool: stat_file — get file metadata without reading content."""
from __future__ import annotations
import json
import os
import stat as stat_mod

NAME = "stat_file"
DESCRIPTION = (
    "Get a file's size, mtime, and permissions WITHOUT reading its content. "
    "Use to check whether a file exists, or to estimate read cost before "
    "calling read_file — a 100KB file is ~25K input tokens on the next "
    "round, so cheap upfront checks pay off. Returns ok:false with "
    "exists:false when the path is missing (not an error)."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File or directory path"},
    },
    "required": ["path"],
}


def handler(path: str) -> str:
    path = os.path.expanduser(path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return json.dumps({"ok": False, "exists": False, "path": path})
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})
    mode = st.st_mode
    kind = (
        "dir" if stat_mod.S_ISDIR(mode)
        else "file" if stat_mod.S_ISREG(mode)
        else "symlink" if stat_mod.S_ISLNK(mode)
        else "other"
    )
    return json.dumps({
        "ok": True,
        "exists": True,
        "path": path,
        "type": kind,
        "size_bytes": st.st_size,
        "mtime": int(st.st_mtime),
        "mode": oct(mode & 0o777),
        "estimated_read_tokens": st.st_size // 4 if kind == "file" else None,
    })
