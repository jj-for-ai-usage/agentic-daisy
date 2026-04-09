"""Agentic Daisy -- 4-Layer Memory Stack.

Adapted from ``milla-jovovich/mempalace``'s ``layers.py`` (MIT licensed).

The memory stack is a session-start hydration strategy that splits memory
access into four cost tiers:

    L0  Identity          ~100 tokens        Always loaded
    L1  Essential Story   ~500-800 tokens    Always loaded
    L2  On-Demand         ~200-500/call      Loaded when a wing/room comes up
    L3  Deep Search       unbounded          Only on explicit query

L0 reads a plain text file (``.daisy/identity.txt``). L1 picks the top-N
records from ``MemoryStore`` sorted by an ``importance`` field, grouped by
room. L2 is a wing/room-filtered listing. **L3 is intentionally omitted**
-- daisy does not ship a vector store; semantic search is substring-only
via ``search_memory``.

``MemoryStack.wake_up()`` returns the L0+L1 text block. The CLI prepends
it to the system prompt at session start so the agent boots with identity
and top context already in-window.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Dict, List, Optional


class Layer0:
    """Plain-text identity file. Read once and cached."""

    def __init__(self, identity_path: str) -> None:
        self.path = identity_path
        self._text: Optional[str] = None

    def render(self) -> str:
        if self._text is not None:
            return self._text
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self._text = f.read().strip()
            except OSError:
                self._text = ""
        else:
            self._text = ""
        if not self._text:
            return ""
        return "## L0 -- IDENTITY\n" + self._text

    def token_estimate(self) -> int:
        return len(self.render()) // 4


class Layer1:
    """Top-N memories by importance, grouped by room. ~500-800 tokens."""

    MAX_MEMORIES = 15
    MAX_CHARS = 3200
    SNIPPET_LEN = 200

    def __init__(self, memory_store, wing: str = "") -> None:
        self.memory_store = memory_store
        self.wing = wing

    def generate(self) -> str:
        records: List[Dict[str, Any]] = list(
            self.memory_store._memories  # noqa: SLF001
        )
        if self.wing:
            records = [m for m in records if m.get("wing", "") == self.wing]
        if not records:
            return ""

        # Score: importance field first, then recency by updated timestamp.
        def _score(mem: Dict[str, Any]) -> tuple:
            imp = mem.get("importance", 0) or 0
            try:
                imp_val = float(imp)
            except (TypeError, ValueError):
                imp_val = 0.0
            updated = mem.get("updated", "") or mem.get("created", "") or ""
            return (imp_val, updated)

        records.sort(key=_score, reverse=True)
        top = records[: self.MAX_MEMORIES]

        # Group by room for readability.
        by_room: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for mem in top:
            by_room[mem.get("room", "") or "_"].append(mem)

        lines = ["## L1 -- ESSENTIAL STORY"]
        total = len(lines[0])
        for room, entries in sorted(by_room.items()):
            room_line = "\n[%s]" % room
            lines.append(room_line)
            total += len(room_line)
            for mem in entries:
                value = (mem.get("value", "") or "").strip().replace("\n", " ")
                if len(value) > self.SNIPPET_LEN:
                    value = value[: self.SNIPPET_LEN - 3] + "..."
                line = "  - %s: %s" % (mem.get("key", "?"), value)
                if total + len(line) > self.MAX_CHARS:
                    lines.append("  ... (more via search_memory)")
                    return "\n".join(lines)
                lines.append(line)
                total += len(line)
        return "\n".join(lines)


class Layer2:
    """Wing/room filtered listing. Returned on explicit recall."""

    def __init__(self, memory_store) -> None:
        self.memory_store = memory_store

    def retrieve(self, wing: str = "", room: str = "",
                 hall: str = "", n_results: int = 10) -> str:
        records: List[Dict[str, Any]] = list(
            self.memory_store._memories  # noqa: SLF001
        )
        matches: List[Dict[str, Any]] = []
        for mem in records:
            if wing and mem.get("wing", "") != wing:
                continue
            if room and mem.get("room", "") != room:
                continue
            if hall and mem.get("hall", "") != hall:
                continue
            matches.append(mem)

        if not matches:
            label = ",".join(filter(None, [
                "wing=" + wing if wing else "",
                "room=" + room if room else "",
                "hall=" + hall if hall else "",
            ])) or "everything"
            return "## L2 -- ON-DEMAND\nNo memories for %s." % label

        matches = matches[:n_results]
        lines = ["## L2 -- ON-DEMAND (%d)" % len(matches)]
        for mem in matches:
            value = (mem.get("value", "") or "").strip().replace("\n", " ")
            if len(value) > 300:
                value = value[:297] + "..."
            room_name = mem.get("room", "?")
            lines.append("  [%s] %s: %s" % (room_name, mem.get("key", "?"), value))
        return "\n".join(lines)


class MemoryStack:
    """L0 + L1 wake-up bundle; L2 retrieval on demand."""

    def __init__(self, memory_store, identity_path: str, wing: str = "") -> None:
        self.memory_store = memory_store
        self.l0 = Layer0(identity_path)
        self.l1 = Layer1(memory_store, wing=wing)
        self.l2 = Layer2(memory_store)

    def wake_up(self) -> str:
        """Return the L0 + L1 text block for session-start injection."""
        parts = []
        l0_text = self.l0.render()
        if l0_text:
            parts.append(l0_text)
        l1_text = self.l1.generate()
        if l1_text:
            parts.append(l1_text)
        return "\n\n".join(parts)

    def recall(self, wing: str = "", room: str = "",
               hall: str = "", n_results: int = 10) -> str:
        return self.l2.retrieve(wing=wing, room=room, hall=hall,
                                n_results=n_results)
