"""Tool: file_summary -- statistical orientation for a large file.

Streams through a file and returns a structured summary: shape, severity
counts, top-K normalized signature lines, detected section markers, and
head/tail excerpts. Lets the agent orient itself in a huge log without
pulling the content into context.
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Deque, List

from ._log_utils import (
    DEFAULT_SECTION_REGEX,
    DEFAULT_SEVERITY_PATTERNS,
    normalize_line,
)

NAME = "file_summary"
DESCRIPTION = (
    "Return a statistical orientation report for a file without reading its "
    "full content: total lines/bytes, first/last N lines, severity counts, "
    "top-K normalized signature lines (deduped by stripping digits/hex/paths/"
    "timestamps), and detected section markers. Default section detection "
    "matches '=== banners ===', '--- banners ---', '# markdown headings', "
    "and '[Section]' lines; override with section_pattern. Severity buckets "
    "default to ERROR/WARN/FATAL/INFO/DEBUG; override with severity_patterns. "
    "Ideal as a first step before diving into an unknown large file."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "top_k": {
            "type": "integer",
            "description": "Top K most-frequent signature lines to return. Default 20.",
        },
        "severity_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Regex patterns used to bucket lines into severity categories. "
                "Default: ['ERROR', 'WARN', 'FATAL', 'INFO', 'DEBUG']."
            ),
        },
        "section_pattern": {
            "type": "string",
            "description": (
                "Optional regex identifying section marker lines. Default "
                "matches ==== banners, ---- banners, # headings, and [Section] lines."
            ),
        },
        "max_bytes_scanned": {
            "type": "integer",
            "description": "Cap scan size in bytes. Default 100 MB.",
        },
        "head_tail_lines": {
            "type": "integer",
            "description": "How many lines to include in first_lines/last_lines. Default 10.",
        },
    },
    "required": ["path"],
}

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_TOP_K = 20
DEFAULT_HEAD_TAIL = 10
MAX_SECTIONS_RETURNED = 100


def handler(
    path: str,
    top_k: int = DEFAULT_TOP_K,
    severity_patterns: List[str] = None,
    section_pattern: str = "",
    max_bytes_scanned: int = DEFAULT_MAX_BYTES,
    head_tail_lines: int = DEFAULT_HEAD_TAIL,
) -> str:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return json.dumps({"error": "not a file or not found", "path": path})

    top_k = max(1, top_k)
    head_tail_lines = max(0, head_tail_lines)
    max_bytes_scanned = max(1, max_bytes_scanned)

    # Compile severity regexes
    if not severity_patterns:
        severity_patterns = list(DEFAULT_SEVERITY_PATTERNS)
    try:
        sev_compiled = [(p, re.compile(p)) for p in severity_patterns]
    except re.error as exc:
        return json.dumps({"error": "invalid severity pattern: %s" % exc})

    # Compile section regex
    if section_pattern:
        try:
            section_rx = re.compile(section_pattern)
        except re.error as exc:
            return json.dumps({"error": "invalid section_pattern: %s" % exc})
    else:
        section_rx = DEFAULT_SECTION_REGEX

    try:
        total_bytes_on_disk = os.path.getsize(path)
    except OSError as exc:
        return json.dumps({"error": str(exc), "path": path})

    total_lines = 0
    total_bytes = 0
    longest_line = 0
    sum_line_length = 0
    truncated = False

    severity_counts = {p: 0 for p in severity_patterns}
    signatures: dict = {}  # normalized -> count
    sections: List[dict] = []

    first_lines: List[str] = []
    tail_buffer: Deque[str] = deque(maxlen=head_tail_lines)

    try:
        with open(path, "r", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n")
                total_lines += 1
                total_bytes += len(raw)
                ll = len(line)
                if ll > longest_line:
                    longest_line = ll
                sum_line_length += ll

                # Head/tail
                if len(first_lines) < head_tail_lines:
                    first_lines.append(line)
                tail_buffer.append(line)

                # Severity buckets
                for name, rx in sev_compiled:
                    if rx.search(line):
                        severity_counts[name] += 1

                # Section markers (cap the list to keep response small)
                if len(sections) < MAX_SECTIONS_RETURNED and section_rx.search(line):
                    sections.append({"line_number": total_lines, "text": line[:200]})

                # Signature counting
                sig = normalize_line(line)
                signatures[sig] = signatures.get(sig, 0) + 1

                if total_bytes >= max_bytes_scanned:
                    truncated = True
                    break
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})

    avg_line_length = (sum_line_length // total_lines) if total_lines else 0

    # Top K signatures
    top_sigs = sorted(signatures.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    top_sigs_out = [{"signature": s, "count": c} for s, c in top_sigs]

    return json.dumps({
        "path": path,
        "total_lines": total_lines,
        "total_bytes_scanned": total_bytes,
        "total_bytes_on_disk": total_bytes_on_disk,
        "longest_line": longest_line,
        "avg_line_length": avg_line_length,
        "first_lines": first_lines,
        "last_lines": list(tail_buffer),
        "severity_counts": severity_counts,
        "signatures": top_sigs_out,
        "unique_signatures": len(signatures),
        "sections": sections,
        "sections_truncated": len(sections) >= MAX_SECTIONS_RETURNED,
        "truncated": truncated,
    })
