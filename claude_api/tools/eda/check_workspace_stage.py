"""Tool: check_workspace_stage — report the current Cadence flow stage of a single workspace.

Walks the 8-stage ladder (SYN, INIT_DESIGN, FLOORPLAN, PLACEOPT, CLOCK,
CLOCKOPT, ROUTE, ROUTEOPT) using the same semantics as the original tcsh
flow-status script: a stage is NOT_AVAILABLE unless the prior stage was
SUCCESS and the current log's mtime is newer than the prior log's mtime.

In addition to the ladder, returns:
  - SYN sub-stage progress parsed from syn/reports/summary_table/final.csv
    (which constraints/pre_gen/syn_gen/map/... completed + per-substage runtime)
  - Per-stage wall runtime (SYN: CSV Real Elapsed; PNR: 'real=' from Ending line)
  - final.csv mtime (for detecting stale csvs)
  - A tail of the CURRENT stage's log (one log only) so the agent can spot
    unexpected errors outside the tool's regex rules.
  - A warnings[] list surfacing silent errors (e.g. CSV parse failure) that
    the agent should pass to the user.
"""
from __future__ import annotations
import csv as _csv
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple

LOG = logging.getLogger("daisy.eda.check_workspace_stage")

NAME = "check_workspace_stage"
DESCRIPTION = (
    "Report where a single Cadence SYN/PNR workspace is in the flow. "
    "Returns current_stage, current_status (SUCCESS/ONGOING/FAIL/NOT_STARTED), "
    "last_completed, and a stages[] ladder with per-stage status, log mtime, "
    "runtime, and a fail_reason when a stage failed. Also returns SYN sub-stage "
    "progress (syn_substages), the final.csv mtime, a tail of the current "
    "stage's log (~40 lines, 4K char cap), current_log_tail_status indicating "
    "whether the tail was read cleanly, and a warnings[] list surfacing any "
    "silent errors the agent should relay to the user. Stages short-circuit: "
    "once any stage is non-SUCCESS, later stages are NOT_AVAILABLE. Use after "
    "scan_workspaces to drill into one trial."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace": {
            "type": "string",
            "description": "Path to the workspace directory (the parent of syn/ and pnr/).",
        },
    },
    "required": ["workspace"],
}


# Flow ladder in execution order: (log_dir_name, display_name)
_PNR_LADDER = [
    ("initdesign", "INIT_DESIGN"),
    ("floorplan",  "FLOORPLAN"),
    ("placeopt",   "PLACEOPT"),
    ("clock",      "CLOCK"),
    ("clockopt",   "CLOCKOPT"),
    ("route",      "ROUTE"),
    ("routeopt",   "ROUTEOPT"),
]

_PNR_FINISH_RE = re.compile(r"Finish plugin.*post.*unconditional")
_SYN_FINAL_ROW_RE = re.compile(r"^final,", re.MULTILINE)
# Innovus "Ending" line: --- Ending "Innovus" (totcpu=..., real=HH:MM:SS[.ms], mem=...) ---
# Captures fractional seconds if present (Innovus sometimes emits "real=34:39:23.125").
_PNR_RUNTIME_RE = re.compile(r"real\s*=\s*([0-9:.]+)")
# Matches Done! at the start of a line (tcsh: grep "^Done!"). Optional leading
# whitespace accommodates logs where output is slightly indented.
_SYN_DONE_RE = re.compile(r"^\s*Done!", re.MULTILINE)

# final.csv column indices (0-based) in data rows
_CSV_SUBSTAGE_NAME_COL = 0
_CSV_REAL_RUNTIME_COL  = 30
_CSV_REAL_ELAPSED_COL  = 32
_CSV_MIN_COLS          = 33  # need at least up through Real Elapsed

_TAIL_LINES     = 40
_TAIL_CHAR_CAP  = 4000
# Size of the bounded read window used for tail operations. 4x char_cap gives
# headroom for logs with unusually long lines; clamped to at least 16 KB.
_TAIL_WINDOW    = max(_TAIL_CHAR_CAP * 4, 16_384)


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _format_mtime(mt: Optional[float]) -> str:
    if mt is None:
        return "NA"
    return datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")


def _read_tail_window(path: str, window: int = _TAIL_WINDOW) -> Tuple[str, str]:
    """Read the last `window` bytes of a file via seek.

    Returns (text, status) where status is one of:
      "ok"         — text was read successfully
      "not_found"  — path is empty or file doesn't exist
      "read_error" — OSError while stat/read (permissions, stale NFS, ...)

    Caller must NOT rely on the first partial line of `text` — it may be a
    mid-line fragment when the window doesn't cover the whole file.
    """
    if not path or not os.path.isfile(path):
        return "", "not_found"
    try:
        size = os.path.getsize(path)
        read_from = max(0, size - window)
        with open(path, "rb") as fh:
            fh.seek(read_from)
            data = fh.read()
    except OSError as exc:
        LOG.debug("tail read failed for %s: %s", path, exc)
        return "", "read_error"
    text = data.decode("utf-8", errors="ignore")
    # Drop mid-line prefix when we started past the beginning of the file.
    if read_from > 0:
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
    return text, "ok"


def _tail_text(path: str, n: int = 2) -> Optional[str]:
    """Return a string joining the last n lines. None on read error / missing."""
    text, status = _read_tail_window(path)
    if status != "ok":
        return None
    lines = text.splitlines(keepends=True)
    return "".join(lines[-n:]) if lines else ""


def _tail_has(path: str, marker: str, n: int = 2) -> bool:
    tail = _tail_text(path, n=n)
    return tail is not None and marker in tail


def _tail_matches(path: str, pattern: "re.Pattern", n: int = 2) -> bool:
    """Regex-anchored variant of _tail_has. Matches across the last n lines."""
    tail = _tail_text(path, n=n)
    return tail is not None and bool(pattern.search(tail))


def _file_has(path: str, pattern: "re.Pattern") -> bool:
    try:
        with open(path, "r", errors="ignore") as fh:
            return any(pattern.search(line) for line in fh)
    except OSError:
        return False


def _parse_syn_substages(final_csv: str) -> Tuple[List[dict], Optional[str]]:
    """Parse final.csv and return (substages, parse_error_msg).

    Presence of a row means the sub-stage ran to completion (it emitted metrics).
    Returns ([], None) if the file doesn't exist. Returns ([], "msg") if
    the file exists but parsing failed — caller should surface in warnings[].
    """
    if not os.path.isfile(final_csv):
        return [], None
    out: List[dict] = []
    try:
        with open(final_csv, "r", errors="ignore") as fh:
            reader = _csv.reader(fh)
            for row in reader:
                if not row or len(row) < _CSV_MIN_COLS:
                    continue
                name = row[_CSV_SUBSTAGE_NAME_COL].strip()
                if not name or name == "Metric":  # header row
                    continue
                out.append({
                    "name": name,
                    "real_runtime": row[_CSV_REAL_RUNTIME_COL].strip(),
                    "real_elapsed": row[_CSV_REAL_ELAPSED_COL].strip(),
                })
    except (OSError, _csv.Error, ValueError, IndexError) as exc:
        msg = "failed to parse final.csv at %s: %s: %s" % (
            final_csv, type(exc).__name__, exc,
        )
        LOG.debug(msg)
        return [], msg
    return out, None


def _scan_pnr_log(log_path: str) -> Tuple[bool, Optional[str]]:
    """Single pass over a PNR log: return (has_finish_marker, last_real_runtime).

    Collapses what used to be two separate full-file scans (_file_has for
    _PNR_FINISH_RE + _extract_pnr_runtime) into one — ~halves I/O for the
    SUCCESS path on multi-hundred-MB Innovus logs.
    """
    found_finish = False
    last_runtime: Optional[str] = None
    try:
        with open(log_path, "r", errors="ignore") as fh:
            for line in fh:
                if not found_finish and _PNR_FINISH_RE.search(line):
                    found_finish = True
                m = _PNR_RUNTIME_RE.search(line)
                if m:
                    last_runtime = m.group(1)
    except OSError:
        return False, None
    return found_finish, last_runtime


def _tail_file(log_path: str, n_lines: int = _TAIL_LINES,
               char_cap: int = _TAIL_CHAR_CAP) -> Tuple[str, str]:
    """Read last ~n_lines of a file via bounded seek.

    Returns (tail_text, status) where status is "ok", "empty", "not_found",
    or "read_error". Agents check status to distinguish a stage that's simply
    silent from one whose log couldn't be read.
    """
    text, read_status = _read_tail_window(log_path)
    if read_status != "ok":
        return "", read_status
    lines = text.splitlines(keepends=True)
    tail = "".join(lines[-n_lines:])
    if not tail:
        return "", "empty"
    if len(tail) > char_cap:
        # Truncate to fit within char_cap INCLUDING the marker.
        marker = "\n... (truncated)"
        keep = max(0, char_cap - len(marker))
        tail = tail[-keep:] + marker
    return tail, "ok"


def _analyze_syn(workspace: str):
    """Return (status, syn_mtime, syn_log, final_csv_path, final_csv_mtime, fail_reason).

    fail_reason is a short token explaining WHY SYN is FAIL, for agent decision-
    making. One of: "csv_missing", "csv_stale", "no_final_row", or None when
    the stage isn't FAIL.
    """
    syn_log = os.path.join(workspace, "syn", "logs", "syn.log")
    final_csv = os.path.join(workspace, "syn", "reports", "summary_table", "final.csv")

    if not os.path.isfile(syn_log):
        return ("NOT_AVAILABLE", None, "", final_csv, None, None)

    syn_mtime = _mtime(syn_log)
    final_csv_mtime = _mtime(final_csv)

    # Line-anchored: matches tcsh `grep "^Done!"`. Rejects mid-line occurrences
    # like "All modules Done! 42 warnings" which shouldn't signal completion.
    if not _tail_matches(syn_log, _SYN_DONE_RE, n=2):
        return ("ONGOING", syn_mtime, syn_log, final_csv, final_csv_mtime, None)

    # Done! — validate via final.csv
    if final_csv_mtime is None:
        return ("FAIL", syn_mtime, syn_log, final_csv, None, "csv_missing")
    if syn_mtime is not None and final_csv_mtime <= syn_mtime:
        return ("FAIL", syn_mtime, syn_log, final_csv, final_csv_mtime, "csv_stale")
    if not _file_has(final_csv, _SYN_FINAL_ROW_RE):
        return ("FAIL", syn_mtime, syn_log, final_csv, final_csv_mtime, "no_final_row")
    return ("SUCCESS", syn_mtime, syn_log, final_csv, final_csv_mtime, None)


def _analyze_pnr_stage(workspace: str, stage_dir: str,
                       prior_mtime: Optional[float], prior_ok: bool):
    """Return (status, mtime, log_path, runtime, fail_reason).

    fail_reason is "no_unconditional_finish" when a PNR stage has an Ending
    line but lacks the unconditional-finish marker; otherwise None.
    """
    if not prior_ok:
        return ("NOT_AVAILABLE", None, "", None, None)

    log = os.path.join(workspace, "pnr", stage_dir, "logs", stage_dir + ".log")
    if not os.path.isfile(log):
        return ("NOT_AVAILABLE", None, "", None, None)

    mt = _mtime(log)
    # Stale-log guard: current log must be newer than the prior stage's log.
    if prior_mtime is not None and mt is not None and mt < prior_mtime:
        return ("NOT_AVAILABLE", mt, log, None, None)

    if not _tail_has(log, "Ending", n=2):
        return ("ONGOING", mt, log, None, None)

    found_finish, runtime = _scan_pnr_log(log)
    if found_finish:
        return ("SUCCESS", mt, log, runtime, None)
    return ("FAIL", mt, log, None, "no_unconditional_finish")


def _derive_summary(stages: list) -> Tuple[Optional[str], str, Optional[str], str]:
    """Return (current_stage, current_status, last_completed, current_log_path)."""
    last_completed: Optional[str] = None
    current_stage: Optional[str] = None
    current_status = "NOT_STARTED"
    current_log = ""

    for s in stages:
        if s["status"] == "SUCCESS":
            last_completed = s["name"]
            current_stage = s["name"]
            current_status = "SUCCESS"
            current_log = s.get("log", "") or ""
        elif s["status"] in ("ONGOING", "FAIL"):
            current_stage = s["name"]
            current_status = s["status"]
            current_log = s.get("log", "") or ""
            break
        # NOT_AVAILABLE — keep walking

    return current_stage, current_status, last_completed, current_log


def make_handler(audit=None, **kwargs):
    def check_workspace_stage(workspace: str = "") -> str:
        t0 = time.time()

        def _emit_audit(success: bool) -> None:
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=success,
                    latency_s=time.time() - t0, round_num=-1,
                )

        # Input guard: empty string silently resolves to cwd; None raises
        # TypeError inside abspath. Both must produce a clean ok:false.
        if not isinstance(workspace, str) or not workspace.strip():
            _emit_audit(False)
            return json.dumps({
                "ok": False,
                "error": "workspace must be a non-empty string path",
                "error_code": "INVALID_INPUT",
            })

        ws = os.path.abspath(workspace)

        if not os.path.isdir(ws):
            _emit_audit(False)
            return json.dumps({
                "ok": False,
                "error": "workspace does not exist or is not a directory: %s" % ws,
                "error_code": "WORKSPACE_NOT_FOUND",
            })

        warnings: List[str] = []

        try:
            stages = []

            # SYN
            (syn_status, syn_mtime, syn_log, final_csv,
             final_csv_mtime, syn_fail_reason) = _analyze_syn(ws)
            syn_substages, parse_error = _parse_syn_substages(final_csv)
            if parse_error is not None:
                warnings.append(parse_error)
            syn_runtime = (
                syn_substages[-1]["real_elapsed"] if syn_substages else None
            )

            stages.append({
                "name": "SYN",
                "status": syn_status,
                "mtime": _format_mtime(syn_mtime),
                "log": syn_log,
                "runtime": syn_runtime,
                "fail_reason": syn_fail_reason,
                "final_csv_mtime": _format_mtime(final_csv_mtime),
                "syn_substages": syn_substages,
            })
            prior_mtime = syn_mtime
            prior_ok = (syn_status == "SUCCESS")

            # PNR ladder
            for dir_name, disp in _PNR_LADDER:
                status, mt, log, runtime, fail_reason = _analyze_pnr_stage(
                    ws, dir_name, prior_mtime, prior_ok,
                )
                stages.append({
                    "name": disp,
                    "status": status,
                    "mtime": _format_mtime(mt),
                    "log": log,
                    "runtime": runtime,
                    "fail_reason": fail_reason,
                })
                if status == "SUCCESS":
                    prior_mtime = mt
                    prior_ok = True
                else:
                    prior_ok = False

            current_stage, current_status, last_completed, current_log = _derive_summary(stages)
            if current_log:
                current_tail, tail_status = _tail_file(current_log)
            else:
                current_tail, tail_status = "", "not_started"

            if tail_status == "read_error":
                warnings.append(
                    "could not read current log tail (permissions or I/O "
                    "error): %s" % current_log
                )

            _emit_audit(True)
            return json.dumps({
                "ok": True,
                "workspace": ws,
                "current_stage": current_stage,
                "current_status": current_status,
                "last_completed": last_completed,
                "stages": stages,
                "current_log_path": current_log,
                "current_log_tail": current_tail,
                "current_log_tail_status": tail_status,
                "warnings": warnings,
            })
        except Exception as exc:
            _emit_audit(False)
            return json.dumps({
                "ok": False,
                "error": "check failed: %s: %s" % (type(exc).__name__, exc),
                "error_code": "UNEXPECTED_ERROR",
                "error_type": type(exc).__name__,
            })

    return check_workspace_stage
