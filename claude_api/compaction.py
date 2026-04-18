"""Agentic Daisy — Conversation compaction (auto-summarize old turns)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")

# Keep the most recent N messages intact when compacting.
_KEEP_RECENT = 4  # ~2 turns (user + assistant each)

_COMPACTION_PROMPT = """\
Summarize this conversation between a user and an AI assistant (Daisy)
using the exact structure below. Omit any section that has no content.

Context: <one-line summary of what the user is trying to accomplish>
Findings: <bullet list of discovered facts, file paths, metrics>
Actions taken: <bullet list of tools called and their outcomes>
Open questions: <bullet list of what's still unresolved or in progress>
Saved memory keys: <comma-separated list of memory keys saved>

Rules:
- Target length: 300-500 words total. Prefer shorter when there's less
  content — do NOT pad.
- Preserve exact strings verbatim for: file paths, memory keys, task IDs,
  batch IDs, error messages, timing slack values, and numeric metrics.
  Paraphrase explanations and commentary.
- Do NOT add analysis, suggestions, or commentary beyond what was
  discussed. This summary replaces the original messages and will be
  read by the assistant to resume work — facts only."""


class ConversationCompactor:
    """Summarizes old conversation turns when context grows too large."""

    def __init__(
        self,
        client,
        model: str = "claude-haiku-4-5",
        threshold_tokens: int = 80_000,
    ) -> None:
        self.client = client
        self.model = model
        self.threshold = threshold_tokens

    def maybe_compact(
        self,
        history: List[Dict[str, Any]],
        last_input_tokens: int,
        audit=None,
    ) -> bool:
        """Compact history in-place if last_input_tokens > threshold.

        Returns True if compaction was performed.
        """
        if last_input_tokens < self.threshold:
            return False
        if len(history) <= _KEEP_RECENT + 2:
            # Not enough messages to compact
            return False

        # We replace the prefix with [user: summary, assistant: ack], so the
        # first message in `to_keep` must be a plain-text user message —
        # NOT a tool_result echo, because the tool_use it pairs with lives
        # in `to_summarize` and will be eaten by summarization, leaving an
        # orphan tool_use_id that the API rejects with 400. Grow the keep
        # window past both non-user messages AND tool_result user messages.
        keep_count = _KEEP_RECENT
        while keep_count < len(history) and (
            history[-keep_count].get("role") != "user"
            or _is_tool_result_message(history[-keep_count])
        ):
            keep_count += 1
        if keep_count >= len(history):
            # Whole history is to_keep; nothing left to summarize.
            return False

        to_summarize = history[:-keep_count]
        to_keep = history[-keep_count:]
        messages_removed = len(to_summarize)

        LOG.info(
            "Compacting conversation: %d messages → summary + %d recent",
            len(history), len(to_keep),
        )

        summary = self._summarize(to_summarize)
        if summary is None:
            # Summarization failed; keep history intact rather than destroy it.
            LOG.warning("Compaction aborted: summarizer unavailable")
            return False

        history.clear()
        history.append({
            "role": "user",
            "content": "[Conversation Summary]\n" + summary,
        })
        history.append({
            "role": "assistant",
            "content": "Understood. I have the conversation context from the summary.",
        })
        history.extend(to_keep)

        if audit is not None:
            audit.log_compaction(
                messages_removed=messages_removed,
                summary_tokens=len(summary.split()),  # rough word count
            )
        return True

    def _summarize(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Call Claude (cheap model) to summarize the old messages."""
        # Format messages into readable text
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                # Tool results or multi-block content
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            texts.append(
                                "[tool: %s(%s)]" % (
                                    block.get("name", "?"),
                                    _truncate(str(block.get("input", "")), 200),
                                )
                            )
                        elif block.get("type") == "tool_result":
                            texts.append(
                                "[result: %s]" % _truncate(
                                    block.get("content", ""), 500,
                                )
                            )
                content = "\n".join(texts)
            parts.append("%s: %s" % (role, _truncate(str(content), 2000)))

        conversation_text = "\n\n".join(parts)
        # Truncate total to avoid blowing the summarization call budget
        if len(conversation_text) > 50_000:
            conversation_text = conversation_text[:50_000] + "\n[...truncated...]"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=_COMPACTION_PROMPT,
                messages=[{"role": "user", "content": conversation_text}],
            )
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    return block.text
            LOG.warning("Compaction response had no text block — keeping history")
            return None
        except Exception as exc:
            LOG.warning("Compaction API call failed: %s — keeping history as-is", exc)
            return None


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _is_tool_result_message(msg: Dict[str, Any]) -> bool:
    """True if this user message carries a tool_result echo rather than
    real user input. Splitting on one of these during compaction
    orphans the paired tool_use and yields a 400 from the API."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )
    return False
