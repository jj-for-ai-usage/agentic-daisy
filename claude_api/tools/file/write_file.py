"""Tool: write_file — write content to a file."""
from __future__ import annotations
import json
import os

NAME = "write_file"
DESCRIPTION = (
    "Write content to a file, OVERWRITING any existing content. Creates "
    "parent directories if needed. Prefer append_file (to add to the end) "
    "or edit_file (to change a portion) unless you genuinely need to "
    "replace the entire file — those are cheaper and non-destructive. "
    "Use write_file for brand-new files or full rewrites only."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to write to"},
        "content": {"type": "string", "description": "Content to write to the file"},
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
            existed = os.path.exists(path)
            with open(path, "w") as f:
                f.write(content)
            if audit is not None:
                audit.log_shell_command(
                    command="write_file %s (%d bytes, %s)" % (
                        path, len(content), "overwrote" if existed else "created",
                    ),
                    exit_code=0, timed_out=False, latency_s=0.0,
                )
            return json.dumps({
                "ok": True,
                "status": "overwrote" if existed else "created",
                "path": path,
                "bytes": len(content),
            })
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc), "path": path})
    return handler
