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
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        self.log_file = os.path.join(
            log_dir, "daisy-%s-%s.jsonl" % (self.session_id, date_str)
        )
        self._model = model
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

    def log_shell_command(
        self,
        command: str,
        exit_code: int,
        timed_out: bool,
        latency_s: float,
    ) -> None:
        self._write({
            "event": "shell_command",
            "command": command,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "latency_s": round(latency_s, 3),
        })

    # Pricing per million tokens (as of 2025 — update if model changes)
    _PRICING = {
        "claude-haiku-4-5":   {"input": 0.80,  "output": 4.00},
        "claude-sonnet-4-5":  {"input": 3.00,  "output": 15.00},
        "claude-opus-4":      {"input": 15.00, "output": 75.00},
    }

    def get_session_cost(self) -> float:
        """Estimate session cost in USD based on token usage."""
        pricing = self._PRICING.get(self._model, {"input": 3.0, "output": 15.0})
        cost = (
            self._total_input_tokens * pricing["input"] / 1_000_000
            + self._total_output_tokens * pricing["output"] / 1_000_000
        )
        return cost

    def get_session_summary(self) -> str:
        """Human-readable session summary with tokens and cost."""
        cost = self.get_session_cost()
        return (
            "Tokens: %d in / %d out | Est. cost: $%.4f"
            % (self._total_input_tokens, self._total_output_tokens, cost)
        )

    def log_session_end(self) -> None:
        cost = self.get_session_cost()
        self._write({
            "event": "session_end",
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "estimated_cost_usd": round(cost, 6),
        })
