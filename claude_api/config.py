"""Agentic Daisy — Configuration."""
from __future__ import annotations

import os
from typing import Optional


DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MEMORY_DIR = os.path.expanduser("~/.daisy/memory")
DEFAULT_LOG_DIR = os.path.expanduser("~/.daisy/logs")


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
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.max_tokens = max_tokens
        self.memory_dir = memory_dir
        self.log_dir = log_dir
        self.system_prompt = system_prompt
        self.debug = debug
