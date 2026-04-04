"""Agentic Daisy — Shell command and Python script execution tools."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audit import AuditLogger

MAX_OUTPUT = 50_000  # characters
MAX_TIMEOUT = 300  # seconds

# Environment variables to strip from subprocesses (prevent key leakage)
_SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "API_KEY"}


def _safe_env() -> dict:
    """Return a copy of os.environ with sensitive keys removed."""
    return {k: v for k, v in os.environ.items() if k not in _SENSITIVE_KEYS}


def make_run_command(audit: AuditLogger, interactive: bool = False):
    """Factory returning a run_command handler with audit and mode context."""

    def run_command(command: str, timeout: int = 30) -> str:
        """Execute a shell command and return stdout + stderr."""
        import time

        # Clamp timeout
        timeout = max(1, min(timeout, MAX_TIMEOUT))

        # Interactive confirmation
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
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_safe_env(),
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

        # Audit log
        audit.log_shell_command(
            command=command,
            exit_code=exit_code,
            timed_out=timed_out,
            latency_s=elapsed,
        )

        # Truncate output
        combined = stdout + stderr
        if len(combined) > MAX_OUTPUT:
            combined = combined[:MAX_OUTPUT]
            truncated = True
        else:
            truncated = False

        return json.dumps({
            "exit_code": exit_code,
            "stdout": stdout[:MAX_OUTPUT] if len(stdout) > MAX_OUTPUT else stdout,
            "stderr": stderr[:MAX_OUTPUT] if len(stderr) > MAX_OUTPUT else stderr,
            "timed_out": timed_out,
            "truncated": truncated,
        })

    return run_command


def make_run_python(audit: AuditLogger, interactive: bool = False,
                    workspace_dir: str = ""):
    """Factory returning a run_python handler with audit and mode context."""
    from datetime import datetime as _dt

    # Set up workspace directory for saving scripts
    if workspace_dir:
        os.makedirs(workspace_dir, mode=0o700, exist_ok=True)

    def run_python(code: str, timeout: int = 60) -> str:
        """Write Python code to workspace, execute it, return output."""
        import time

        timeout = max(1, min(timeout, MAX_TIMEOUT))

        # Interactive confirmation with code preview
        if interactive:
            lines = code.splitlines()
            preview = "\n".join("    " + ln for ln in lines[:10])
            if len(lines) > 10:
                preview += "\n    ... (%d more lines)" % (len(lines) - 10)
            print("\n  Python script (%d lines):\n%s" % (len(lines), preview))
            try:
                answer = input("  Execute? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer not in ("y", "yes"):
                return json.dumps({"status": "cancelled", "reason": "User declined"})

        # Write script to workspace (kept for inspection) or fall back to /tmp
        if workspace_dir:
            stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            script_path = os.path.join(workspace_dir, "script_%s.py" % stamp)
            with open(script_path, "w") as f:
                f.write(code)
        else:
            fd, script_path = tempfile.mkstemp(suffix=".py", prefix="daisy-")
            with os.fdopen(fd, "w") as f:
                f.write(code)

        t0 = time.time()
        timed_out = False
        try:
            python_cmd = os.environ.get("DAISY_PYTHON", "python3::3.9.9")
            proc = subprocess.run(
                [python_cmd, script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_safe_env(),
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = -1
            stdout = ""
            stderr = "Script timed out after %d seconds" % timeout
        except Exception as exc:
            exit_code = -1
            stdout = ""
            stderr = "Failed to execute: %s" % exc

        elapsed = time.time() - t0

        audit.log_shell_command(
            command="%s <script:%d lines>" % (python_cmd, len(code.splitlines())),
            exit_code=exit_code,
            timed_out=timed_out,
            latency_s=elapsed,
        )

        return json.dumps({
            "exit_code": exit_code,
            "stdout": stdout[:MAX_OUTPUT] if len(stdout) > MAX_OUTPUT else stdout,
            "stderr": stderr[:MAX_OUTPUT] if len(stderr) > MAX_OUTPUT else stderr,
            "timed_out": timed_out,
            "truncated": len(stdout) + len(stderr) > MAX_OUTPUT,
            "script_path": script_path,
        })

    return run_python
