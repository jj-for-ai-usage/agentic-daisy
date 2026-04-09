"""Agentic Daisy -- Configuration."""
from __future__ import annotations

import logging
import os
import stat
from typing import Optional

LOG = logging.getLogger("daisy")

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096

# Data directories default to .daisy/ inside the project root.
# DAISY_ROOT is set by the bash launchers (bin/daisy etc.)
_PROJECT_ROOT = os.environ.get("DAISY_ROOT", os.getcwd())
DEFAULT_MEMORY_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "memory")
DEFAULT_KG_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "kg")
DEFAULT_LOG_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "logs")
DEFAULT_SESSION_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "sessions")
DEFAULT_WORKSPACE_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "workspace")
DEFAULT_SKILLS_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "skills")
DEFAULT_TASK_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "tasks")
DEFAULT_BATCH_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "batches")
DEFAULT_IDENTITY_FILE = os.path.join(_PROJECT_ROOT, ".daisy", "identity.txt")

# User-level persistent dirs (shared across projects, next to ~/.daisy/api_key)
_USER_HOME = os.path.expanduser("~/.daisy")
USER_SKILLS_DIR = os.path.join(_USER_HOME, "skills")
USER_TOOLS_DIR = os.path.join(_USER_HOME, "tools")
DEFAULT_CUSTOM_TOOLS_DIR = os.path.join(_PROJECT_ROOT, ".daisy", "tools")

# API key file stays in home dir (should NOT be inside the git repo)
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

    _KNOWN_MODELS = frozenset({
        "claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4",
        "claude-sonnet-4-6", "claude-opus-4-6",
    })

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        memory_dir: str = DEFAULT_MEMORY_DIR,
        kg_dir: str = DEFAULT_KG_DIR,
        log_dir: str = DEFAULT_LOG_DIR,
        workspace_dir: str = DEFAULT_WORKSPACE_DIR,
        skills_dir: str = DEFAULT_SKILLS_DIR,
        task_dir: str = DEFAULT_TASK_DIR,
        batch_dir: str = DEFAULT_BATCH_DIR,
        identity_file: str = DEFAULT_IDENTITY_FILE,
        system_prompt: Optional[str] = None,
        debug: bool = False,
        api_key_file: str = DEFAULT_API_KEY_FILE,
        budget: Optional[float] = None,
        compaction_threshold: int = 80_000,
        temperature: float = 0.3,
        max_tool_rounds: int = 20,
    ) -> None:
        # Priority: explicit arg > env var > key file
        # Use None-aware checks so empty strings don't break the chain.
        self.api_key = (
            api_key
            if api_key
            else os.environ.get("ANTHROPIC_API_KEY")
            or _load_api_key_file(api_key_file)
            or ""
        )
        self.model = model
        self.max_tokens = max_tokens
        self.memory_dir = memory_dir
        self.kg_dir = kg_dir
        self.identity_file = identity_file
        self.log_dir = log_dir
        self.workspace_dir = workspace_dir
        self.skills_dir = skills_dir
        self.task_dir = task_dir
        self.batch_dir = batch_dir
        self.system_prompt = system_prompt
        self.debug = debug
        self.budget = budget
        self.compaction_threshold = compaction_threshold
        self.temperature = temperature
        self.max_tool_rounds = max_tool_rounds

        # --- Validation (fail fast on bad config) ---
        if self.max_tokens <= 0 or self.max_tokens > 128_000:
            raise ValueError(
                "max_tokens must be between 1 and 128000, got %d" % self.max_tokens
            )
        if self.budget is not None and self.budget <= 0:
            raise ValueError(
                "budget must be > 0 or None (unlimited), got %s" % self.budget
            )
        if not (0.0 <= self.temperature <= 1.0):
            raise ValueError(
                "temperature must be between 0.0 and 1.0, got %s" % self.temperature
            )
        if self.max_tool_rounds <= 0:
            raise ValueError(
                "max_tool_rounds must be > 0, got %d" % self.max_tool_rounds
            )
        if self.model not in self._KNOWN_MODELS:
            # Don't block -- dated versions like claude-sonnet-4-5-20250514 are valid
            LOG.warning(
                "Unknown model '%s'. Known: %s",
                self.model, ", ".join(sorted(self._KNOWN_MODELS)),
            )
