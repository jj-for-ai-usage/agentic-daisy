"""Tool: delete_file — delete a single file (not a directory)."""
from __future__ import annotations
import json
import os

NAME = "delete_file"
DESCRIPTION = (
    "Delete a single file. Logged to the audit trail. Will NOT delete "
    "directories (use run_command for that, with explicit user confirmation). "
    "Returns ok:false with a clear error if the path is a directory, does "
    "not exist, or is outside the project workspace."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to delete"},
    },
    "required": ["path"],
}


def make_handler(audit=None, **kwargs):
    def handler(path: str) -> str:
        resolved = os.path.expanduser(path)
        if not os.path.exists(resolved):
            return json.dumps({"ok": False, "error": "File not found", "path": resolved})
        if os.path.isdir(resolved):
            return json.dumps({
                "ok": False,
                "error": "Path is a directory. Use run_command with explicit "
                         "user confirmation to remove directories.",
                "path": resolved,
            })
        try:
            size = os.path.getsize(resolved)
            os.remove(resolved)
        except OSError as exc:
            return json.dumps({"ok": False, "error": str(exc), "path": resolved})
        if audit is not None:
            # Piggyback on shell_command log — delete_file is a mutation worth recording.
            audit.log_shell_command(
                command="delete_file %s" % resolved,
                exit_code=0, timed_out=False, latency_s=0.0,
            )
        return json.dumps({
            "ok": True, "status": "deleted", "path": resolved, "size_bytes": size,
        })
    return handler
