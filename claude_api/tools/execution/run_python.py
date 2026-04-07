"""Tool: run_python -- execute a Python script."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile

NAME = "run_python"
DESCRIPTION = (
    "Execute a Python script and return stdout, stderr, and exit code. "
    "Use for multi-line data processing, log analysis, config parsing, "
    "or any complex logic."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "Python code to execute"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60, max: 300)"},
    },
    "required": ["code"],
}

MAX_OUTPUT = 50_000
MAX_TIMEOUT = 300
_SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "API_KEY"}


def _safe_env():
    return {k: v for k, v in os.environ.items() if k not in _SENSITIVE_KEYS}


def make_handler(audit=None, interactive=False, workspace_dir="", **kwargs):
    from datetime import datetime as _dt

    if workspace_dir:
        os.makedirs(workspace_dir, mode=0o700, exist_ok=True)

    def run_python(code: str, timeout: int = 60) -> str:
        import time
        timeout = max(1, min(timeout, MAX_TIMEOUT))

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

        # Write script to workspace (kept) or /tmp (fallback)
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
            python_cmd = os.environ.get("DAISY_PYTHON", "python3")
            proc = subprocess.run(
                [python_cmd, script_path], capture_output=True, text=True,
                timeout=timeout, env=_safe_env(),
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
        if audit is not None:
            audit.log_shell_command(
                command="%s <script:%d lines>" % (python_cmd, len(code.splitlines())),
                exit_code=exit_code, timed_out=timed_out, latency_s=elapsed,
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
