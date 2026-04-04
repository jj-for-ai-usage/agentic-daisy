"""Tool: get_env — system environment snapshot."""
from __future__ import annotations
import json
import os
import platform
import shutil

NAME = "get_env"
DESCRIPTION = (
    "Get a system environment snapshot: hostname, user, cwd, "
    "Python version, OS, disk usage. One call instead of multiple shell commands."
)
INPUT_SCHEMA = {"type": "object", "properties": {}}


def handler() -> str:
    info = {
        "hostname": platform.node(),
        "user": os.environ.get("USER", os.environ.get("LOGNAME", "unknown")),
        "cwd": os.getcwd(),
        "python_version": platform.python_version(),
        "os": "%s %s" % (platform.system(), platform.release()),
        "arch": platform.machine(),
    }
    try:
        usage = shutil.disk_usage(os.getcwd())
        info["disk_total_gb"] = round(usage.total / (1024 ** 3), 1)
        info["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
        info["disk_used_pct"] = round((usage.used / usage.total) * 100, 1)
    except OSError:
        pass
    return json.dumps(info)
