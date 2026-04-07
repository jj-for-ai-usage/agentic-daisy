"""Agentic Daisy -- Audit logging (JSON Lines, no content logged)."""
from __future__ import annotations

import json
import logging
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

LOG = logging.getLogger("daisy")


class BudgetExceededError(Exception):
    """Raised when session cost exceeds the configured budget."""

    def __init__(self, cost: float, budget: float) -> None:
        self.cost = cost
        self.budget = budget
        super().__init__(
            "Session budget of $%.2f exceeded (current: $%.4f)" % (budget, cost)
        )


class AuditLogger:
    """Append-only JSONL audit logger. One file per session."""

    def __init__(self, log_dir: str, model: str, budget: Optional[float] = None) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self.log_dir = log_dir
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        self.log_file = os.path.join(
            log_dir, "daisy-%s-%s.jsonl" % (self.session_id, date_str)
        )
        self._model = model
        self._budget = budget  # None = unlimited
        self._warned_budget = False
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cache_read_tokens = 0
        self._total_cache_write_tokens = 0
        self._log_session_start(model)

    # ------------------------------------------------------------------
    def _write(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        event["session_id"] = self.session_id
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as exc:
            LOG.debug("Audit write failed: %s", exc)  # best-effort; never crash the session

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
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cache_read_tokens += cache_read_tokens
        self._total_cache_write_tokens += cache_write_tokens
        self._write({
            "event": "api_call",
            "round_num": round_num,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_read_tokens": cache_read_tokens,
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

    # Pricing per million tokens (as of 2025 -- update if model changes)
    _PRICING = {
        "claude-haiku-4-5":   {"input": 0.80,  "output": 4.00},
        "claude-sonnet-4-5":  {"input": 3.00,  "output": 15.00},
        "claude-opus-4":      {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00},
        "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    }

    def get_session_cost(self) -> float:
        """Estimate session cost in USD based on token usage.

        Cached reads cost 90% less than normal input tokens.
        Cache writes cost 25% more than normal input tokens.
        Non-cached input tokens are charged at the standard rate.
        """
        pricing = self._PRICING.get(self._model, {"input": 3.0, "output": 15.0})
        input_rate = pricing["input"] / 1_000_000
        output_rate = pricing["output"] / 1_000_000
        # Non-cached input = total input minus cached reads
        uncached_input = max(0, self._total_input_tokens - self._total_cache_read_tokens)
        cost = (
            uncached_input * input_rate
            + self._total_cache_read_tokens * input_rate * 0.1    # 90% discount
            + self._total_cache_write_tokens * input_rate * 1.25  # 25% surcharge
            + self._total_output_tokens * output_rate
        )
        return cost

    def get_session_summary(self) -> str:
        """Human-readable session summary with tokens and cost."""
        cost = self.get_session_cost()
        summary = "Tokens: %d in / %d out | Est. cost: $%.4f" % (
            self._total_input_tokens, self._total_output_tokens, cost,
        )
        if self._total_cache_read_tokens > 0:
            summary += " | Cache: %d read, %d write" % (
                self._total_cache_read_tokens, self._total_cache_write_tokens,
            )
        return summary

    def check_budget(self) -> str:
        """Check cost against budget. Returns 'ok', 'warning', or 'exceeded'."""
        if self._budget is None:
            return "ok"
        cost = self.get_session_cost()
        if cost >= self._budget:
            self._write({
                "event": "budget_exceeded",
                "cost_usd": round(cost, 6),
                "budget_usd": self._budget,
            })
            return "exceeded"
        if not self._warned_budget and cost >= self._budget * 0.8:
            self._warned_budget = True
            self._write({
                "event": "budget_warning",
                "cost_usd": round(cost, 6),
                "budget_usd": self._budget,
            })
            return "warning"
        return "ok"

    def log_compaction(
        self, messages_removed: int, summary_tokens: int,
    ) -> None:
        self._write({
            "event": "compaction",
            "messages_removed": messages_removed,
            "summary_tokens": summary_tokens,
        })

    def log_tool_cache_hit(self, tool_name: str, round_num: int) -> None:
        self._write({
            "event": "tool_cache_hit",
            "round_num": round_num,
            "tool_name": tool_name,
        })

    def log_batch_submit(
        self, batch_id: str, request_count: int,
        model: str, estimated_tokens: int,
    ) -> None:
        self._write({
            "event": "batch_submit",
            "batch_id": batch_id,
            "request_count": request_count,
            "model": model,
            "estimated_input_tokens": estimated_tokens,
        })

    def log_batch_complete(
        self, batch_id: str, model: str,
        input_tokens: int, output_tokens: int,
        succeeded: int, errored: int,
    ) -> None:
        pricing = self._PRICING.get(model, {"input": 3.0, "output": 15.0})
        cost = (
            input_tokens * pricing["input"] * 0.5 / 1_000_000
            + output_tokens * pricing["output"] * 0.5 / 1_000_000
        )
        self._write({
            "event": "batch_complete",
            "batch_id": batch_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "succeeded": succeeded,
            "errored": errored,
            "cost_usd_50pct": round(cost, 6),
        })

    def log_session_end(self) -> None:
        cost = self.get_session_cost()
        self._write({
            "event": "session_end",
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cache_read_tokens": self._total_cache_read_tokens,
            "total_cache_write_tokens": self._total_cache_write_tokens,
            "estimated_cost_usd": round(cost, 6),
        })
