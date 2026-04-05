"""Agentic Daisy -- Persistent batch job tracker."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")

_VALID_STATUSES = ("processing", "ended", "results_retrieved", "expired", "failed")


def _safe_filename(custom_id: str) -> str:
    """Sanitize custom_id for safe use as a filename."""
    safe = re.sub(r'[^a-zA-Z0-9_\-.]', '_', custom_id)
    safe = safe.strip('.')[:200]
    return safe or "unnamed"


class BatchStore:
    """Tracks batch API jobs persisted to batches.json.

    Individual results are saved as separate files under results_dir so
    Daisy can read one result at a time without loading everything.
    """

    def __init__(self, batch_dir: str) -> None:
        self._dir = batch_dir
        os.makedirs(batch_dir, mode=0o700, exist_ok=True)
        self._file = os.path.join(batch_dir, "batches.json")
        self._results_root = os.path.join(batch_dir, "results")
        self._batches: List[Dict[str, Any]] = []
        self._load()

    # -- persistence ------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._file):
            self._batches = []
            return
        try:
            with open(self._file, "r") as f:
                self._batches = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Batch file corrupted (%s), starting empty.", exc)
            self._batches = []

    def _save(self) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(self._batches, f, indent=2, default=str)
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
        existing = [b["id"] for b in self._batches
                    if b["id"].startswith("batch_%s_" % date_str)]
        seq = len(existing) + 1
        return "batch_%s_%03d" % (date_str, seq)

    # -- CRUD -------------------------------------------------

    def create_batch(
        self,
        task_id: str,
        model: str,
        manifest: List[Dict[str, str]],
        batch_api_id: str,
        expires_at: str,
        estimated_input_tokens: int = 0,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        batch_id = self._generate_id()
        results_dir = os.path.join(self._results_root, batch_id)
        os.makedirs(results_dir, mode=0o700, exist_ok=True)
        record = {
            "id": batch_id,
            "batch_api_id": batch_api_id,
            "task_id": task_id,
            "model": model,
            "status": "processing",
            "request_count": len(manifest),
            "manifest": manifest,
            "request_counts": None,
            "submitted_at": now,
            "expires_at": expires_at,
            "ended_at": None,
            "results_dir": results_dir,
            "estimated_input_tokens": estimated_input_tokens,
            "actual_input_tokens": None,
            "actual_output_tokens": None,
            "updated": now,
        }
        self._batches.append(record)
        self._save()
        return json.dumps({"status": "created", "batch_id": batch_id})

    def update_batch(self, batch_id: str, **fields) -> str:
        batch = self._find(batch_id)
        if batch is None:
            return json.dumps({"error": "Batch '%s' not found" % batch_id})
        for key, val in fields.items():
            if key in batch:
                batch[key] = val
        batch["updated"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return json.dumps({"status": "updated", "batch": batch})

    def get_batch(self, batch_id: str) -> str:
        batch = self._find(batch_id)
        if batch is None:
            return json.dumps({"error": "Batch '%s' not found" % batch_id})
        return json.dumps({"batch": batch})

    def list_batches(
        self,
        status: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> str:
        results = self._batches
        if status:
            results = [b for b in results if b["status"] == status]
        if task_id:
            results = [b for b in results if b.get("task_id") == task_id]
        summaries = []
        for b in results:
            summaries.append({
                "id": b["id"],
                "task_id": b["task_id"],
                "model": b["model"],
                "status": b["status"],
                "request_count": b["request_count"],
                "submitted_at": b["submitted_at"],
                "expires_at": b["expires_at"],
                "ended_at": b.get("ended_at"),
            })
        return json.dumps({"count": len(summaries), "batches": summaries})

    def get_pending_batches(self) -> List[Dict[str, Any]]:
        """Return all batches with status 'processing' (for system prompt summary)."""
        return [b for b in self._batches if b["status"] == "processing"]

    # -- result file I/O --------------------------------------

    def save_result(
        self,
        batch_id: str,
        custom_id: str,
        text: str,
        status: str = "succeeded",
    ) -> str:
        batch = self._find(batch_id)
        if batch is None:
            return json.dumps({"error": "Batch '%s' not found" % batch_id})
        results_dir = batch["results_dir"]
        os.makedirs(results_dir, exist_ok=True)
        safe_name = _safe_filename(custom_id)
        path = os.path.join(results_dir, safe_name + ".json")
        try:
            with open(path, "w") as f:
                json.dump({"custom_id": custom_id, "status": status, "text": text}, f)
        except OSError as exc:
            return json.dumps({"error": "Failed to write result: %s" % exc})
        return json.dumps({"status": "saved", "path": path})

    def read_result(self, batch_id: str, custom_id: str) -> str:
        batch = self._find(batch_id)
        if batch is None:
            return json.dumps({"error": "Batch '%s' not found" % batch_id})
        safe_name = _safe_filename(custom_id)
        path = os.path.join(batch["results_dir"], safe_name + ".json")
        if not os.path.exists(path):
            return json.dumps({"error": "Result '%s' not found" % custom_id})
        try:
            with open(path, "r") as f:
                return f.read()
        except OSError as exc:
            return json.dumps({"error": str(exc)})

    # -- helpers -----------------------------------------------

    def _find(self, batch_id: str) -> Optional[Dict[str, Any]]:
        for b in self._batches:
            if b["id"] == batch_id:
                return b
        return None
