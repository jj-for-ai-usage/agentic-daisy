"""Agentic Daisy — Conversation compaction (auto-summarize old turns)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")

# Keep the most recent N messages intact when compacting.
_KEEP_RECENT = 4  # ~2 turns (user + assistant each)

_COMPACTION_PROMPT = """\
Summarize this conversation between a user and an AI assistant (Daisy).
Preserve ALL of the following if present:
- Key findings and conclusions
- File paths, directory structures, and command outputs mentioned
- Errors encountered and how they were resolved
- Decisions made and their rationale
- Memory keys that were saved (save_memory calls)
- Tool results that informed later decisions
- Active tool names the assistant used or referenced
- The assistant's role and system identity (Daisy, EDA assistant)
- Current working directory and active project paths
- Active task IDs and their current status

Be concise but complete — this summary replaces the original messages.
Do NOT add commentary or analysis beyond what was discussed."""


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

        to_summarize = history[:-_KEEP_RECENT]
        to_keep = history[-_KEEP_RECENT:]
        messages_removed = len(to_summarize)

        LOG.info(
            "Compacting conversation: %d messages → summary + %d recent",
            len(history), len(to_keep),
        )

        summary = self._summarize(to_summarize)

        history.clear()
        history.append({
            "role": "user",
            "content": "[Conversation Summary]\n" + summary,
        })
        history.append({
            "role": "assistant",
            "content": "Understood. I have the prior context. I'll check memory and tasks if I need more detail.",
        })
        history.extend(to_keep)

        if audit is not None:
            audit.log_compaction(
                messages_removed=messages_removed,
                summary_tokens=len(summary.split()),  # rough word count
            )
        return True

    def _summarize(self, messages: List[Dict[str, Any]]) -> str:
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
                max_tokens=2048,
                system=_COMPACTION_PROMPT,
                messages=[{"role": "user", "content": conversation_text}],
            )
            if not response.content:
                raise ValueError("Empty response from compaction model")
            return response.content[0].text
        except Exception as exc:
            LOG.warning("Compaction API call failed: %s — keeping history as-is", exc)
            # Return a minimal fallback summary
            return "[Compaction failed: %s. Previous conversation had %d messages.]" % (
                exc, len(parts),
            )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
