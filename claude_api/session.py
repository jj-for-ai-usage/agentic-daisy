"""Agentic Daisy — Session persistence (save/resume conversations)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")

DEFAULT_SESSION_DIR = os.path.expanduser("~/.daisy/sessions")


class SessionManager:
    """Manages saving and loading conversation sessions."""

    def __init__(self, session_dir: str = DEFAULT_SESSION_DIR) -> None:
        self.session_dir = session_dir
        os.makedirs(session_dir, mode=0o700, exist_ok=True)

    def _session_path(self, name: str) -> str:
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
        if not safe_name:
            safe_name = "default"
        return os.path.join(self.session_dir, safe_name + ".json")

    def load(self, name: str) -> Optional[List[Dict[str, Any]]]:
        """Load a session's conversation history. Returns None if not found."""
        path = self._session_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return data.get("history", [])
        except (json.JSONDecodeError, ValueError) as exc:
            LOG.warning("Corrupted session file %s: %s", path, exc)
            return None

    def save(self, name: str, history: List[Dict[str, Any]]) -> None:
        """Save conversation history to session file (atomic write)."""
        path = self._session_path(name)
        data = {
            "name": name,
            "updated": datetime.now(timezone.utc).isoformat(),
            "turns": len(history),
            "history": history,
        }
        fd, tmp_path = tempfile.mkstemp(
            dir=self.session_dir, suffix=".tmp", prefix=".session-",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all available sessions with metadata."""
        sessions = []
        for fname in sorted(os.listdir(self.session_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.session_dir, fname)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                sessions.append({
                    "name": data.get("name", fname[:-5]),
                    "turns": data.get("turns", 0),
                    "updated": data.get("updated", ""),
                })
            except (json.JSONDecodeError, ValueError):
                sessions.append({
                    "name": fname[:-5],
                    "turns": 0,
                    "updated": "corrupted",
                })
        return sessions
