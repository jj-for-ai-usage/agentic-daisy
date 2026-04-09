"""Shared helpers for log-oriented search tools.

Used by file_summary, compare_logs, and any future tool that needs to
normalize log lines or bucket them by severity.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Tuple

# Default severity patterns -- substrings the user can override via a param.
DEFAULT_SEVERITY_PATTERNS = ["ERROR", "WARN", "FATAL", "INFO", "DEBUG"]

# Medium normalization regexes, applied in order. Each tuple is (compiled, replacement).
_NORM_PATTERNS = [
    # ISO-8601 timestamps (e.g. 2024-03-15T14:22:31.123Z or 2024-03-15 14:22:31)
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"), "<TS>"),
    # Bare clock timestamps (e.g. 14:22:31.123 or 14:22:31)
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<TS>"),
    # Hex numbers (0xDEADBEEF, must come before generic digit rule)
    (re.compile(r"0x[0-9a-fA-F]+"), "<HEX>"),
    # Absolute/relative paths -- consume any non-whitespace run starting with /
    (re.compile(r"(?<![a-zA-Z0-9_])/\S+"), "<PATH>"),
    # Plain integer and float runs (come last so they don't eat timestamps)
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<N>"),
]


def normalize_line(line: str) -> str:
    """Apply medium normalization: strip timestamps, hex, paths, and numbers.

    The goal is to collapse lines that are structurally identical but differ
    only in their variable payload (e.g. "Processed 12 items at 0x3f" and
    "Processed 847 items at 0x7b" both normalize to
    "Processed <N> items at <HEX>").
    """
    out = line.rstrip("\n")
    for rx, repl in _NORM_PATTERNS:
        out = rx.sub(repl, out)
    return out


def bucket_by_severity(
    lines: Iterable[str], patterns: List[str] = None
) -> Dict[str, int]:
    """Count how many lines match each severity pattern.

    A line contributes to the count of EVERY pattern it matches (a line
    containing "ERROR" and "FATAL" would be counted in both buckets). This
    matches what a user eyeballing a log file would intuit.
    """
    if patterns is None:
        patterns = DEFAULT_SEVERITY_PATTERNS
    compiled = [(p, re.compile(p)) for p in patterns]
    counts = {p: 0 for p in patterns}
    for line in lines:
        for name, rx in compiled:
            if rx.search(line):
                counts[name] += 1
    return counts


def signature_counts(
    path: str, top_k: int = 20, max_bytes: int = 100 * 1024 * 1024
) -> Tuple[Counter, int, int, bool]:
    """Stream through a file and return normalized-signature counts.

    Returns a 4-tuple: (Counter of signatures, total_lines_scanned,
    total_bytes_read, truncated_flag).

    Uses streaming reads so it works on files much larger than RAM. Caller
    can call .most_common(top_k) on the Counter.
    """
    counter: Counter = Counter()
    total_lines = 0
    total_bytes = 0
    truncated = False
    with open(path, "r", errors="replace") as f:
        for line in f:
            total_lines += 1
            total_bytes += len(line)
            counter[normalize_line(line)] += 1
            if total_bytes >= max_bytes:
                truncated = True
                break
    return counter, total_lines, total_bytes, truncated


# Default section marker pattern: banner lines with ====/---- or '#'-prefixed headers
DEFAULT_SECTION_REGEX = re.compile(
    r"(?:^\s*={3,}.*={3,}\s*$)"    # ===== Phase: Foo =====
    r"|(?:^\s*-{3,}.*-{3,}\s*$)"   # ----- Foo -----
    r"|(?:^#{1,6}\s+\S)"            # # Heading, ## Subheading
    r"|(?:^\s*\[[^\]]+\]\s*$)"      # [Section]
)
