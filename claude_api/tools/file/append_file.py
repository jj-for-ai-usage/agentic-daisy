"""Tool: append_file — append content to end of a file."""
from __future__ import annotations
import json
import os

NAME = "append_file"
DESCRIPTION = (
    "Append content to the end of a file without reading it first. "
    "Creates the file if it doesn't exist. Cheaper than read-then-write "
    "when you only need to add new content at the end."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to append to"},
        "content": {"type": "string", "description": "Content to append"},
    },
    "required": ["path", "content"],
}


def make_handler(audit=None, **kwargs):
    def handler(path: str, content: str) -> str:
        path = os.path.expanduser(path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a") as f:
                f.write(content)
            if audit is not None:
                audit.log_shell_command(
                    command="append_file %s (+%d bytes)" % (path, len(content)),
                    exit_code=0, timed_out=False, latency_s=0.0,
                )
            return json.dumps({
                "ok": True, "status": "appended", "path": path,
                "bytes_added": len(content),
            })
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc), "path": path})
    return handler
