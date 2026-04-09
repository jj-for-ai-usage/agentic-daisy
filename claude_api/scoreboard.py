"""Agentic Daisy -- Tool usage scoreboard.

Tracks per-tool call counts, error counts, cumulative time, and last-used
timestamp across sessions. Persisted to <log_dir>/tool_scoreboard.json.

The single update point is ToolRegistry.execute(). Every tool call flows
through execute(), which records into a ToolScoreboard if one has been
attached to the registry. Scoreboard errors are always swallowed: a broken
scoreboard must never break tool execution.

Concurrency: flock-based locking on a sibling lockfile, matching the
MemoryStore pattern. Atomic write via temp file + os.replace().
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator

try:
    import fcntl  # POSIX only
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

LOG = logging.getLogger("daisy")

_SCHEMA_VERSION = 1
_FILE_NAME = "tool_scoreboard.json"
_LOCK_NAME = ".tool_scoreboard.lock"


class ToolScoreboard:
    """Per-tool usage counters persisted to a JSON file.

    Record shape:
      {
        "version": 1,
        "updated": "<ISO8601>",
        "tools": {
          "<tool_name>": {
            "calls": int,
            "errors": int,
            "total_ms": int,
            "last_used": "<ISO8601>"
          }
        }
      }
    """

    def __init__(self, log_dir: str) -> None:
        self._dir = log_dir
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        self._file = os.path.join(log_dir, _FILE_NAME)
        self._lock_file = os.path.join(log_dir, _LOCK_NAME)
        self._state: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------ IO
    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Best-effort exclusive lock; degrades silently if flock is unavailable."""
        if not _HAS_FCNTL:
            yield
            return
        try:
            lf = open(self._lock_file, "a+")
        except OSError as exc:
            LOG.debug("scoreboard lockfile open failed: %s", exc)
            yield
            return
        try:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                LOG.debug("scoreboard flock failed: %s", exc)
            yield
        finally:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lf.close()

    def _load(self) -> None:
        if not os.path.exists(self._file):
            self._state = {}
            return
        try:
            with open(self._file, "r") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            LOG.warning(
                "scoreboard file %s unreadable: %s -- starting empty",
                self._file, exc,
            )
            self._state = {}
            return
        if not isinstance(raw, dict) or not isinstance(raw.get("tools"), dict):
            LOG.warning("scoreboard file has unexpected shape -- starting empty")
            self._state = {}
            return
        self._state = raw["tools"]

    def _save(self) -> None:
        doc = {
            "version": _SCHEMA_VERSION,
            "updated": datetime.now(timezone.utc).isoformat(),
            "tools": self._state,
        }
        fd, tmp_path = tempfile.mkstemp(
            dir=self._dir, suffix=".tmp", prefix=".scoreboard-",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(doc, f, indent=2)
            os.replace(tmp_path, self._file)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ API
    def record(self, name: str, elapsed_ms: int, error: bool) -> None:
        """Record a single tool call. Silent on failure."""
        try:
            with self._locked():
                # Reload under lock so we don't clobber another session's updates
                self._load()
                bucket = self._state.get(name) or {
                    "calls": 0, "errors": 0, "total_ms": 0, "last_used": None,
                }
                bucket["calls"] = int(bucket.get("calls", 0)) + 1
                if error:
                    bucket["errors"] = int(bucket.get("errors", 0)) + 1
                bucket["total_ms"] = int(bucket.get("total_ms", 0)) + max(0, int(elapsed_ms))
                bucket["last_used"] = datetime.now(timezone.utc).isoformat()
                self._state[name] = bucket
                self._save()
        except Exception as exc:
            LOG.debug("scoreboard record failed for %s: %s", name, exc)

    @property
    def path(self) -> str:
        return self._file
