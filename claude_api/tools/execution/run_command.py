"""Tool: run_command -- execute a shell command."""
from __future__ import annotations
import json
import os
import subprocess

NAME = "run_command"
DESCRIPTION = "Execute a shell command on the server and return stdout, stderr, and exit code."
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Shell command to execute (via /bin/sh -c)"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max: 300)"},
    },
    "required": ["command"],
}

MAX_OUTPUT = 50_000
MAX_TIMEOUT = 300
_SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "API_KEY"}


def _safe_env():
    return {k: v for k, v in os.environ.items() if k not in _SENSITIVE_KEYS}


def make_handler(audit=None, interactive=False, **kwargs):
    def run_command(command: str, timeout: int = 30) -> str:
        import time
        timeout = max(1, min(timeout, MAX_TIMEOUT))

        if interactive:
            print("\n  Command: %s" % command)
            try:
                answer = input("  Execute? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer not in ("y", "yes"):
                return json.dumps({"status": "cancelled", "reason": "User declined"})

        t0 = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, env=_safe_env(),
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = -1
            stdout = ""
            stderr = "Command timed out after %d seconds" % timeout
        except Exception as exc:
            exit_code = -1
            stdout = ""
            stderr = "Failed to execute: %s" % exc

        elapsed = time.time() - t0
        if audit is not None:
            audit.log_shell_command(command=command, exit_code=exit_code,
                                    timed_out=timed_out, latency_s=elapsed)

        return json.dumps({
            "exit_code": exit_code,
            "stdout": stdout[:MAX_OUTPUT] if len(stdout) > MAX_OUTPUT else stdout,
            "stderr": stderr[:MAX_OUTPUT] if len(stderr) > MAX_OUTPUT else stderr,
            "timed_out": timed_out,
            "truncated": len(stdout) + len(stderr) > MAX_OUTPUT,
        })
    return run_command
