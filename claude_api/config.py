"""Agentic Daisy — Configuration."""
from __future__ import annotations

import logging
import os
import stat
from typing import Optional

LOG = logging.getLogger("daisy")

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MEMORY_DIR = os.path.expanduser("~/.daisy/memory")
DEFAULT_LOG_DIR = os.path.expanduser("~/.daisy/logs")
DEFAULT_API_KEY_FILE = os.path.expanduser("~/.daisy/api_key")


def _load_api_key_file(path: str) -> str:
    """Read API key from a file. Warns if permissions are too open."""
    if not os.path.exists(path):
        return ""
    try:
        mode = os.stat(path).st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            LOG.warning(
                "API key file %s is readable by others (mode %o). "
                "Fix with:  chmod 600 %s",
                path, mode & 0o777, path,
            )
        with open(path, "r") as f:
            key = f.read().strip()
        if key:
            LOG.debug("Loaded API key from %s", path)
        return key
    except OSError as exc:
        LOG.warning("Could not read API key file %s: %s", path, exc)
        return ""


class DaisyConfig:
    """All configuration for a Daisy session."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        memory_dir: str = DEFAULT_MEMORY_DIR,
        log_dir: str = DEFAULT_LOG_DIR,
        system_prompt: Optional[str] = None,
        debug: bool = False,
        api_key_file: str = DEFAULT_API_KEY_FILE,
    ) -> None:
        # Priority: explicit arg > env var > key file
        self.api_key = (
            api_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
            or _load_api_key_file(api_key_file)
        )
        self.model = model
        self.max_tokens = max_tokens
        self.memory_dir = memory_dir
        self.log_dir = log_dir
        self.system_prompt = system_prompt
        self.debug = debug
