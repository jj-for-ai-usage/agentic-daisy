"""Agentic Daisy — Audit logging (JSON Lines, no content logged)."""
from __future__ import annotations

import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional


class AuditLogger:
    """Append-only JSONL audit logger. One file per session."""

    def __init__(self, log_dir: str, model: str) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        self.log_file = os.path.join(
            log_dir, "daisy-%s-%s.jsonl" % (self.session_id, date_str)
        )
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._log_session_start(model)

    # ------------------------------------------------------------------
    def _write(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        event["session_id"] = self.session_id
        with open(self.log_file, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    # ------------------------------------------------------------------
    def _log_session_start(self, model: str) -> None:
        try:
            import anthropic
            sdk_version = anthropic.__version__
        except Exception:
            sdk_version = "unknown"
        self._write({
            "event": "session_start",
            "model": model,
            "python_version": platform.python_version(),
            "anthropic_sdk_version": sdk_version,
        })

    def log_api_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        stop_reason: Optional[str],
        latency_s: float,
        round_num: int,
    ) -> None:
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._write({
            "event": "api_call",
            "round_num": round_num,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "latency_s": round(latency_s, 3),
        })

    def log_tool_execution(
        self,
        tool_name: str,
        success: bool,
        latency_s: float,
        round_num: int,
    ) -> None:
        self._write({
            "event": "tool_execution",
            "round_num": round_num,
            "tool_name": tool_name,
            "success": success,
            "latency_s": round(latency_s, 3),
        })

    def log_session_end(self) -> None:
        self._write({
            "event": "session_end",
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
        })
