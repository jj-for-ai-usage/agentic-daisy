"""Agentic Daisy — Persistent memory system (JSON file-backed)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")


class MemoryStore:
    """Simple key-value memory store persisted as a single JSON file."""

    def __init__(self, memory_dir: str) -> None:
        self.memory_dir = memory_dir
        os.makedirs(memory_dir, mode=0o700, exist_ok=True)
        self.memory_file = os.path.join(memory_dir, "memories.json")
        self._memories: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.memory_file):
            self._memories = []
            return
        try:
            with open(self.memory_file, "r") as f:
                self._memories = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            LOG.warning(
                "Corrupted memory file %s: %s — starting with empty memories",
                self.memory_file, exc,
            )
            self._memories = []

    def _save(self) -> None:
        # Atomic write: write to temp file, then rename (safe on POSIX)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.memory_dir, suffix=".tmp", prefix=".memories-",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._memories, f, indent=2, default=str)
            os.replace(tmp_path, self.memory_file)
        except BaseException:
            # Clean up temp file if rename failed
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_memory(
        self,
        key: str,
        value: str,
        tags: Optional[List[str]] = None,
    ) -> str:
        """Save or update a memory. Returns confirmation string."""
        if tags is None:
            tags = []

        # Check if key already exists
        for mem in self._memories:
            if mem["key"] == key:
                mem["value"] = value
                mem["tags"] = tags
                mem["updated"] = self._now_iso()
                self._save()
                return json.dumps({"status": "updated", "key": key})

        # New entry
        self._memories.append({
            "key": key,
            "value": value,
            "tags": tags,
            "created": self._now_iso(),
            "updated": self._now_iso(),
        })
        self._save()
        return json.dumps({"status": "created", "key": key})

    def search_memory(
        self,
        query: str = "",
        tag: str = "",
    ) -> str:
        """Search memories by keyword or tag. Returns JSON array of matches."""
        results = []
        for mem in self._memories:
            # Tag filter
            if tag and tag not in mem.get("tags", []):
                continue
            # Keyword filter (substring match on key and value)
            if query:
                q = query.lower()
                if q not in mem["key"].lower() and q not in mem["value"].lower():
                    continue
            results.append(mem)

        if not results:
            return json.dumps({"matches": 0, "results": []})
        return json.dumps({"matches": len(results), "results": results}, default=str)

    def delete_memory(self, key: str) -> str:
        """Delete a memory by key. Returns confirmation or not-found."""
        for i, mem in enumerate(self._memories):
            if mem["key"] == key:
                self._memories.pop(i)
                self._save()
                return json.dumps({"status": "deleted", "key": key})
        return json.dumps({"status": "not_found", "key": key})

    def list_memories(self) -> str:
        """List all memory keys with tags and timestamps."""
        summary = []
        for mem in self._memories:
            summary.append({
                "key": mem["key"],
                "tags": mem.get("tags", []),
                "updated": mem.get("updated", mem.get("created", "")),
            })
        return json.dumps({"total": len(summary), "memories": summary})
