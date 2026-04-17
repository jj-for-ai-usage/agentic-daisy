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
"""
from __future__ import annotations
import csv as _csv
import json
import logging
import os
import re
import time
from datetime import datetime

LOG = logging.getLogger("daisy.eda.check_workspace_stage")

NAME = "check_workspace_stage"
DESCRIPTION = (
    "Report where a single Cadence SYN/PNR workspace is in the flow. "
    "Returns current_stage, current_status (SUCCESS/ONGOING/FAIL/NOT_STARTED), "
    "last_completed, and a stages[] ladder with per-stage status, log mtime, "
    "and runtime. Also returns SYN sub-stage progress (syn_substages), the "
    "final.csv mtime, and a tail of the current stage's log (~40 lines, 4K "
    "char cap) so you can spot errors the rule-based checks may have missed. "
    "Stages short-circuit: once any stage is non-SUCCESS, later stages are "
    "NOT_AVAILABLE. Use after scan_workspaces to drill into one trial."
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


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _format_mtime(mt) -> str:
    if mt is None:
        return "NA"
    return datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")


def _tail_has(path: str, marker: str, n: int = 2) -> bool:
    try:
        with open(path, "r", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return False
    tail = "".join(lines[-n:]) if lines else ""
    return marker in tail


def _tail_matches(path: str, pattern: "re.Pattern", n: int = 2) -> bool:
    """Regex-anchored variant of _tail_has. Matches across the last n lines."""
    try:
        with open(path, "r", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return False
    tail = "".join(lines[-n:]) if lines else ""
    return bool(pattern.search(tail))


def _file_has(path: str, pattern: "re.Pattern") -> bool:
    try:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if pattern.search(line):
                    return True
    except OSError:
        return False
    return False


def _parse_syn_substages(final_csv: str):
    """Parse final.csv and return list of {name, real_runtime, real_elapsed}.

    Presence of a row means the sub-stage ran to completion (it emitted metrics).
    Returns [] if the file doesn't exist or can't be parsed.
    """
    if not os.path.isfile(final_csv):
        return []
    out = []
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
    except Exception as exc:  # pragma: no cover
        LOG.debug("failed to parse %s: %s", final_csv, exc)
        return []
    return out


def _extract_pnr_runtime(log_path: str):
    """Return last-occurrence `real=HH:MM:SS` value from an Innovus log, or None."""
    last = None
    try:
        with open(log_path, "r", errors="ignore") as fh:
            for line in fh:
                m = _PNR_RUNTIME_RE.search(line)
                if m:
                    last = m.group(1)
    except OSError:
        return None
    return last


def _tail_file(log_path: str, n_lines: int = _TAIL_LINES,
               char_cap: int = _TAIL_CHAR_CAP) -> str:
    """Read last ~n_lines of a file without loading the whole file into memory.

    Seeks from the end in a bounded window, splits on newline, keeps the last
    n_lines. Cadence logs routinely exceed a GB; readlines() would OOM.
    """
    if not log_path or not os.path.isfile(log_path):
        return ""
    # Read a window that's comfortably larger than char_cap so n_lines fits.
    # Each log line is typically < 200 chars; 4x char_cap gives plenty of headroom.
    window = max(char_cap * 4, 16_384)
    try:
        size = os.path.getsize(log_path)
        read_from = max(0, size - window)
        with open(log_path, "rb") as fh:
            fh.seek(read_from)
            data = fh.read()
    except OSError as exc:
        LOG.debug("tail read failed for %s: %s", log_path, exc)
        return ""
    text = data.decode("utf-8", errors="ignore")
    # If we started mid-line, drop the partial first line.
    if read_from > 0:
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
    lines = text.splitlines(keepends=True)
    tail = "".join(lines[-n_lines:])
    if len(tail) > char_cap:
        # Truncate to fit within char_cap INCLUDING the marker.
        marker = "\n... (truncated)"
        keep = max(0, char_cap - len(marker))
        tail = tail[-keep:] + marker
    return tail


def _analyze_syn(workspace: str):
    """Return (status, mtime, log_path, final_csv_path)."""
    syn_log = os.path.join(workspace, "syn", "logs", "syn.log")
    final_csv = os.path.join(workspace, "syn", "reports", "summary_table", "final.csv")

    if not os.path.isfile(syn_log):
        return ("NOT_AVAILABLE", None, "", final_csv)

    syn_mtime = _mtime(syn_log)

    # ONGOING: no "Done!" in the last 2 lines yet
    # Line-anchored: matches tcsh `grep "^Done!"`. Rejects mid-line occurrences
    # like "All modules Done! 42 warnings" which shouldn't signal completion.
    if not _tail_matches(syn_log, _SYN_DONE_RE, n=2):
        return ("ONGOING", syn_mtime, syn_log, final_csv)

    # Done! — validate via final.csv
    if os.path.isfile(final_csv):
        csv_mtime = _mtime(final_csv)
        if csv_mtime and syn_mtime and csv_mtime > syn_mtime:
            if _file_has(final_csv, _SYN_FINAL_ROW_RE):
                return ("SUCCESS", syn_mtime, syn_log, final_csv)
    return ("FAIL", syn_mtime, syn_log, final_csv)


def _analyze_pnr_stage(workspace: str, stage_dir: str,
                       prior_mtime, prior_ok: bool):
    """Return (status, mtime, log_path)."""
    if not prior_ok:
        return ("NOT_AVAILABLE", None, "")

    log = os.path.join(workspace, "pnr", stage_dir, "logs", stage_dir + ".log")
    if not os.path.isfile(log):
        return ("NOT_AVAILABLE", None, "")

    mt = _mtime(log)
    if prior_mtime is not None and mt is not None and mt < prior_mtime:
        return ("NOT_AVAILABLE", mt, log)

    if not _tail_has(log, "Ending", n=2):
        return ("ONGOING", mt, log)

    if _file_has(log, _PNR_FINISH_RE):
        return ("SUCCESS", mt, log)
    return ("FAIL", mt, log)


def _derive_summary(stages: list):
    """Return (current_stage, current_status, last_completed, current_log_path)."""
    last_completed = None
    current_stage = None
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

        # Input guard: empty string silently resolves to cwd; None raises
        # TypeError inside abspath. Both must produce a clean ok:false.
        if not isinstance(workspace, str) or not workspace.strip():
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False,
                    latency_s=time.time() - t0, round_num=-1,
                )
            return json.dumps({
                "ok": False,
                "error": "workspace must be a non-empty string path",
            })

        ws = os.path.abspath(workspace)

        if not os.path.isdir(ws):
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False,
                    latency_s=time.time() - t0, round_num=-1,
                )
            return json.dumps({
                "ok": False,
                "error": "workspace does not exist or is not a directory: %s" % ws,
            })

        try:
            stages = []

            # SYN
            syn_status, syn_mtime, syn_log, final_csv = _analyze_syn(ws)
            syn_substages = _parse_syn_substages(final_csv)
            syn_runtime = (
                syn_substages[-1]["real_elapsed"] if syn_substages else None
            ) or None
            final_csv_mtime_str = _format_mtime(_mtime(final_csv))

            stages.append({
                "name": "SYN",
                "status": syn_status,
                "mtime": _format_mtime(syn_mtime),
                "log": syn_log,
                "runtime": syn_runtime,
                "final_csv_mtime": final_csv_mtime_str,
                "syn_substages": syn_substages,
            })
            prior_mtime = syn_mtime
            prior_ok = (syn_status == "SUCCESS")

            # PNR ladder
            for dir_name, disp in _PNR_LADDER:
                status, mt, log = _analyze_pnr_stage(
                    ws, dir_name, prior_mtime, prior_ok,
                )
                rt = _extract_pnr_runtime(log) if (status == "SUCCESS" and log) else None
                stages.append({
                    "name": disp,
                    "status": status,
                    "mtime": _format_mtime(mt),
                    "log": log,
                    "runtime": rt,
                })
                if status == "SUCCESS":
                    prior_mtime = mt
                    prior_ok = True
                else:
                    prior_ok = False

            current_stage, current_status, last_completed, current_log = _derive_summary(stages)
            current_tail = _tail_file(current_log) if current_log else ""

            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=True,
                    latency_s=time.time() - t0, round_num=-1,
                )

            return json.dumps({
                "ok": True,
                "workspace": ws,
                "current_stage": current_stage,
                "current_status": current_status,
                "last_completed": last_completed,
                "stages": stages,
                "current_log_path": current_log,
                "current_log_tail": current_tail,
            })
        except Exception as exc:
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False,
                    latency_s=time.time() - t0, round_num=-1,
                )
            return json.dumps({"ok": False, "error": "check failed: %s" % exc})

    return check_workspace_stage
