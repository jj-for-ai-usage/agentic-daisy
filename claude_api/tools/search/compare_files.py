"""Tool: compare_files -- structured comparison of two text files.

Works on any text files (logs, configs, reports, source). Five modes,
each returning structured JSON (not diff text):
  - signatures:      counts of normalized lines only-in-A / only-in-B / diverged
  - first_divergence: first line where normalized forms differ
  - severity_delta:   per-severity counts in both + delta
  - grep_both:        same regex against both files, matches side-by-side
  - unique_lines:     set difference of normalized line sets
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List

from ._log_utils import (
    DEFAULT_SEVERITY_PATTERNS,
    bucket_by_severity,
    normalize_line,
    signature_counts,
)

NAME = "compare_files"
DESCRIPTION = (
    "Structured comparison of two TEXT files (logs, configs, reports, source) "
    "using pattern-level normalized analysis, not byte-level diff. Modes: "
    "'signatures' (default, new/missing/diverged normalized lines), "
    "'first_divergence' (first line where normalized forms differ), "
    "'severity_delta' (per-bucket counts), 'grep_both' (same regex against "
    "both; requires pattern), 'unique_lines' (set difference). Ideal for "
    "'why did run B break when run A passed?' and config-drift investigations. "
    "For byte-perfect diffs use run_command('diff -u')."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path_a": {"type": "string"},
        "path_b": {"type": "string"},
        "mode": {
            "type": "string",
            "enum": [
                "signatures",
                "first_divergence",
                "severity_delta",
                "grep_both",
                "unique_lines",
            ],
            "description": "Comparison mode. Default: signatures.",
        },
        "pattern": {
            "type": "string",
            "description": "Regex pattern (required for mode='grep_both').",
        },
        "severity_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Severity regexes for severity_delta mode. "
                "Default: ['ERROR', 'WARN', 'FATAL', 'INFO', 'DEBUG']."
            ),
        },
        "max_items_per_bucket": {
            "type": "integer",
            "description": "Cap on items returned per bucket. Default 50.",
        },
        "max_bytes_scanned": {
            "type": "integer",
            "description": "Per-file scan cap in bytes. Default 100 MB.",
        },
    },
    "required": ["path_a", "path_b"],
}

DEFAULT_MAX_ITEMS = 50
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
MAX_GREP_CONTEXT = 2
MAX_GREP_MATCHES = 50


def _validate_files(path_a: str, path_b: str):
    """Return error JSON string if invalid, else None."""
    for p in (path_a, path_b):
        if not os.path.isfile(p):
            return json.dumps({"error": "not a file or not found", "path": p})
    return None


def _mode_signatures(
    path_a: str, path_b: str, max_items: int, max_bytes: int
) -> str:
    ca, lines_a, bytes_a, trunc_a = signature_counts(path_a, top_k=0, max_bytes=max_bytes)
    cb, lines_b, bytes_b, trunc_b = signature_counts(path_b, top_k=0, max_bytes=max_bytes)

    only_in_a: List[dict] = []
    only_in_b: List[dict] = []
    diverged: List[dict] = []

    for sig, count_a in ca.items():
        count_b = cb.get(sig, 0)
        if count_b == 0:
            only_in_a.append({"signature": sig, "count": count_a})
        elif count_a != count_b:
            # Significant divergence: ratio > 2x or absolute delta > 10
            delta = count_a - count_b
            ratio_ok = (count_a >= 2 * count_b) or (count_b >= 2 * count_a)
            if abs(delta) > 10 or ratio_ok:
                diverged.append({
                    "signature": sig,
                    "count_a": count_a,
                    "count_b": count_b,
                    "delta": delta,
                })
    for sig, count_b in cb.items():
        if sig not in ca:
            only_in_b.append({"signature": sig, "count": count_b})

    only_in_a.sort(key=lambda d: d["count"], reverse=True)
    only_in_b.sort(key=lambda d: d["count"], reverse=True)
    diverged.sort(key=lambda d: abs(d["delta"]), reverse=True)

    return json.dumps({
        "mode": "signatures",
        "path_a": path_a,
        "path_b": path_b,
        "lines_a": lines_a,
        "lines_b": lines_b,
        "truncated_a": trunc_a,
        "truncated_b": trunc_b,
        "only_in_a_count": len(only_in_a),
        "only_in_b_count": len(only_in_b),
        "diverged_count": len(diverged),
        "only_in_a": only_in_a[:max_items],
        "only_in_b": only_in_b[:max_items],
        "diverged": diverged[:max_items],
    })


def _mode_first_divergence(path_a: str, path_b: str) -> str:
    try:
        with open(path_a, "r", errors="replace") as fa, \
             open(path_b, "r", errors="replace") as fb:
            lineno = 0
            while True:
                lineno += 1
                la = fa.readline()
                lb = fb.readline()
                if not la and not lb:
                    return json.dumps({
                        "mode": "first_divergence",
                        "path_a": path_a,
                        "path_b": path_b,
                        "diverged": False,
                        "hint": "files are structurally identical after normalization",
                    })
                na = normalize_line(la.rstrip("\n")) if la else ""
                nb = normalize_line(lb.rstrip("\n")) if lb else ""
                if na != nb:
                    return json.dumps({
                        "mode": "first_divergence",
                        "path_a": path_a,
                        "path_b": path_b,
                        "diverged": True,
                        "line_number": lineno,
                        "line_a": la.rstrip("\n") if la else None,
                        "line_b": lb.rstrip("\n") if lb else None,
                        "normalized_a": na,
                        "normalized_b": nb,
                    })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _mode_severity_delta(
    path_a: str, path_b: str, patterns: List[str]
) -> str:
    if not patterns:
        patterns = list(DEFAULT_SEVERITY_PATTERNS)

    def _count(path):
        with open(path, "r", errors="replace") as f:
            return bucket_by_severity(f, patterns)

    try:
        a = _count(path_a)
        b = _count(path_b)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    delta = {k: b.get(k, 0) - a.get(k, 0) for k in patterns}
    return json.dumps({
        "mode": "severity_delta",
        "path_a": path_a,
        "path_b": path_b,
        "patterns": patterns,
        "a": a,
        "b": b,
        "delta": delta,
    })


def _grep_file(path: str, rx, max_matches: int) -> List[dict]:
    matches: List[dict] = []
    with open(path, "r", errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n")
            if rx.search(line):
                matches.append({"line_number": lineno, "line": line})
                if len(matches) >= max_matches:
                    break
    return matches


def _mode_grep_both(
    path_a: str, path_b: str, pattern: str, max_items: int
) -> str:
    if not pattern:
        return json.dumps({"error": "grep_both mode requires 'pattern'"})
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"error": "invalid regex: %s" % exc})
    max_items = min(max_items, MAX_GREP_MATCHES)
    try:
        a = _grep_file(path_a, rx, max_items)
        b = _grep_file(path_b, rx, max_items)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "mode": "grep_both",
        "path_a": path_a,
        "path_b": path_b,
        "pattern": pattern,
        "matches_a": a,
        "matches_b": b,
        "count_a": len(a),
        "count_b": len(b),
    })


def _mode_unique_lines(
    path_a: str, path_b: str, max_items: int, max_bytes: int
) -> str:
    ca, _la, _ba, trunc_a = signature_counts(path_a, top_k=0, max_bytes=max_bytes)
    cb, _lb, _bb, trunc_b = signature_counts(path_b, top_k=0, max_bytes=max_bytes)
    set_a = set(ca.keys())
    set_b = set(cb.keys())
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    return json.dumps({
        "mode": "unique_lines",
        "path_a": path_a,
        "path_b": path_b,
        "only_in_a_count": len(only_a),
        "only_in_b_count": len(only_b),
        "only_in_a": only_a[:max_items],
        "only_in_b": only_b[:max_items],
        "truncated_a": trunc_a,
        "truncated_b": trunc_b,
    })


def handler(
    path_a: str,
    path_b: str,
    mode: str = "signatures",
    pattern: str = "",
    severity_patterns: List[str] = None,
    max_items_per_bucket: int = DEFAULT_MAX_ITEMS,
    max_bytes_scanned: int = DEFAULT_MAX_BYTES,
) -> str:
    path_a = os.path.expanduser(path_a)
    path_b = os.path.expanduser(path_b)
    err = _validate_files(path_a, path_b)
    if err:
        return err

    max_items = max(1, max_items_per_bucket)
    max_bytes = max(1, max_bytes_scanned)

    if mode == "signatures":
        return _mode_signatures(path_a, path_b, max_items, max_bytes)
    if mode == "first_divergence":
        return _mode_first_divergence(path_a, path_b)
    if mode == "severity_delta":
        return _mode_severity_delta(path_a, path_b, severity_patterns or [])
    if mode == "grep_both":
        return _mode_grep_both(path_a, path_b, pattern, max_items)
    if mode == "unique_lines":
        return _mode_unique_lines(path_a, path_b, max_items, max_bytes)

    return json.dumps({
        "error": "invalid mode",
        "valid_modes": [
            "signatures", "first_divergence", "severity_delta",
            "grep_both", "unique_lines",
        ],
    })
