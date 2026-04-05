"""Agentic Daisy -- Persistent task tracker (structured, multi-session)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")

_VALID_STATUSES = ("active", "paused", "done", "blocked")
_VALID_SUBTASK_STATUSES = ("pending", "active", "done", "skipped")


class TaskStore:
    """Structured task tracker persisted to tasks.json.

    Designed for multi-session workflows and future subagent assignment.
    Follows the same atomic-write + corruption-recovery pattern as MemoryStore.
    """

    def __init__(self, task_dir: str) -> None:
        self._dir = task_dir
        os.makedirs(task_dir, mode=0o700, exist_ok=True)
        self._file = os.path.join(task_dir, "tasks.json")
        self._tasks: List[Dict[str, Any]] = []
        self._load()

    # -- persistence ------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._file):
            self._tasks = []
            return
        try:
            with open(self._file, "r") as f:
                self._tasks = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Task file corrupted (%s), starting empty.", exc)
            self._tasks = []

    def _save(self) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(self._tasks, f, indent=2, default=str)
            os.replace(tmp_path, self._file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -- ID generation ----------------------------------------

    def _generate_id(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        existing = [t["id"] for t in self._tasks if t["id"].startswith("task_%s_" % date_str)]
        seq = len(existing) + 1
        return "task_%s_%03d" % (date_str, seq)

    # -- CRUD -------------------------------------------------

    def create_task(
        self,
        name: str,
        description: str = "",
        priority: str = "medium",
        tags: Optional[List[str]] = None,
        subtasks: Optional[List[Dict[str, str]]] = None,
        context: str = "",
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        task_id = self._generate_id()
        # Normalize subtasks
        subs = []
        if subtasks:
            for s in subtasks:
                subs.append({
                    "name": s.get("name", ""),
                    "status": s.get("status", "pending"),
                    "notes": s.get("notes", ""),
                })
        task = {
            "id": task_id,
            "name": name,
            "status": "active",
            "priority": priority,
            "tags": tags or [],
            "description": description,
            "subtasks": subs,
            "context": context,
            "created": now,
            "updated": now,
        }
        self._tasks.append(task)
        self._save()
        return json.dumps({"status": "created", "task_id": task_id})

    def update_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        context: Optional[str] = None,
        add_subtask: Optional[Dict[str, str]] = None,
        update_subtask: Optional[Dict[str, Any]] = None,
    ) -> str:
        task = self._find(task_id)
        if task is None:
            return json.dumps({"error": "Task '%s' not found" % task_id})

        if status is not None:
            if status not in _VALID_STATUSES:
                return json.dumps({"error": "Invalid status '%s'. Use: %s" % (
                    status, ", ".join(_VALID_STATUSES))})
            task["status"] = status

        if notes is not None:
            task["context"] = (task.get("context", "") + "\n" + notes).strip()

        if context is not None:
            task["context"] = context

        if add_subtask is not None:
            task["subtasks"].append({
                "name": add_subtask.get("name", ""),
                "status": add_subtask.get("status", "pending"),
                "notes": add_subtask.get("notes", ""),
            })

        if update_subtask is not None:
            idx = update_subtask.get("index")
            if idx is not None and 0 <= idx < len(task["subtasks"]):
                sub = task["subtasks"][idx]
                if "status" in update_subtask:
                    if update_subtask["status"] in _VALID_SUBTASK_STATUSES:
                        sub["status"] = update_subtask["status"]
                if "notes" in update_subtask:
                    sub["notes"] = update_subtask["notes"]
            else:
                return json.dumps({"error": "Invalid subtask index: %s" % idx})

        task["updated"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return json.dumps({"status": "updated", "task": task})

    def list_tasks(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> str:
        results = self._tasks
        if status:
            results = [t for t in results if t["status"] == status]
        if tag:
            results = [t for t in results if tag in t.get("tags", [])]
        # Return summary (no full context to save tokens)
        summaries = []
        for t in results:
            done_subs = sum(1 for s in t["subtasks"] if s["status"] == "done")
            total_subs = len(t["subtasks"])
            summaries.append({
                "id": t["id"],
                "name": t["name"],
                "status": t["status"],
                "priority": t["priority"],
                "tags": t["tags"],
                "subtasks": "%d/%d done" % (done_subs, total_subs) if total_subs else "none",
                "updated": t["updated"],
            })
        return json.dumps({"count": len(summaries), "tasks": summaries})

    def get_task(self, task_id: str) -> str:
        task = self._find(task_id)
        if task is None:
            return json.dumps({"error": "Task '%s' not found" % task_id})
        return json.dumps({"task": task})

    # -- helpers -----------------------------------------------

    def _find(self, task_id: str) -> Optional[Dict[str, Any]]:
        for t in self._tasks:
            if t["id"] == task_id:
                return t
        return None
