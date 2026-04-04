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


def make_run_python(audit: AuditLogger, interactive: bool = False):
    """Factory returning a run_python handler with audit and mode context."""

    def run_python(code: str, timeout: int = 60) -> str:
        """Write Python code to a temp file, execute it, return output."""
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

        # Write code to temp file
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="daisy-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)

            t0 = time.time()
            timed_out = False
            try:
                proc = subprocess.run(
                    ["python3", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
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
                command="python3 <script:%d lines>" % len(code.splitlines()),
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
            })
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return run_python
