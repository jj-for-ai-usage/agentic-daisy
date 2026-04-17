"""Tool: check_workspace_stage — report the current Cadence flow stage of a single workspace.

Walks the 8-stage ladder (SYN, INIT_DESIGN, FLOORPLAN, PLACEOPT, CLOCK,
CLOCKOPT, ROUTE, ROUTEOPT) using the same semantics as the original tcsh
flow-status script: a stage is NOT_AVAILABLE unless the prior stage was
SUCCESS and the current log's mtime is newer than the prior log's mtime.
"""
from __future__ import annotations
import json
import os
import re
import time
from datetime import datetime

NAME = "check_workspace_stage"
DESCRIPTION = (
    "Report where a single Cadence SYN/PNR workspace is in the flow. "
    "Returns current_stage, current_status (SUCCESS/ONGOING/FAIL/NOT_STARTED), "
    "last_completed, and a full stages[] ladder with per-stage status and log "
    "mtime. Stages short-circuit: once any stage is non-SUCCESS, all later "
    "stages are NOT_AVAILABLE (matches the tcsh flow-status convention). "
    "Use after scan_workspaces to drill into one trial."
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
_SYN_FINAL_ROW_RE = re.compile(r"final,")


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


def _file_has(path: str, pattern: "re.Pattern") -> bool:
    try:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if pattern.search(line):
                    return True
    except OSError:
        return False
    return False


def _analyze_syn(workspace: str):
    """Return (status, mtime, log_path)."""
    syn_log = os.path.join(workspace, "syn", "logs", "syn.log")
    if not os.path.isfile(syn_log):
        return ("NOT_AVAILABLE", None, "")

    syn_mtime = _mtime(syn_log)

    # ONGOING: no "Done!" in the last 2 lines yet
    if not _tail_has(syn_log, "Done!", n=2):
        return ("ONGOING", syn_mtime, syn_log)

    # Done! — validate via final.csv
    final_csv = os.path.join(workspace, "syn", "reports", "summary_table", "final.csv")
    if os.path.isfile(final_csv):
        csv_mtime = _mtime(final_csv)
        if csv_mtime and syn_mtime and csv_mtime > syn_mtime:
            if _file_has(final_csv, _SYN_FINAL_ROW_RE):
                return ("SUCCESS", syn_mtime, syn_log)
    return ("FAIL", syn_mtime, syn_log)


def _analyze_pnr_stage(workspace: str, stage_dir: str,
                       prior_mtime, prior_ok: bool):
    """Return (status, mtime, log_path)."""
    if not prior_ok:
        return ("NOT_AVAILABLE", None, "")

    log = os.path.join(workspace, "pnr", stage_dir, "logs", stage_dir + ".log")
    if not os.path.isfile(log):
        return ("NOT_AVAILABLE", None, "")

    mt = _mtime(log)
    # Stale-log check: this stage must be newer than the prior stage
    if prior_mtime is not None and mt is not None and mt < prior_mtime:
        return ("NOT_AVAILABLE", mt, log)

    if not _tail_has(log, "Ending", n=2):
        return ("ONGOING", mt, log)

    if _file_has(log, _PNR_FINISH_RE):
        return ("SUCCESS", mt, log)
    return ("FAIL", mt, log)


def _derive_summary(stages: list):
    """From the ladder, pick current_stage/current_status/last_completed."""
    last_completed = None
    current_stage = None
    current_status = "NOT_STARTED"

    for s in stages:
        if s["status"] == "SUCCESS":
            last_completed = s["name"]
            current_stage = s["name"]
            current_status = "SUCCESS"
        elif s["status"] in ("ONGOING", "FAIL"):
            current_stage = s["name"]
            current_status = s["status"]
            break
        # NOT_AVAILABLE — ignore, keep walking (nothing new to report)

    return current_stage, current_status, last_completed


def make_handler(audit=None, **kwargs):
    def check_workspace_stage(workspace: str) -> str:
        t0 = time.time()
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
            syn_status, syn_mtime, syn_log = _analyze_syn(ws)
            stages.append({
                "name": "SYN", "status": syn_status,
                "mtime": _format_mtime(syn_mtime), "log": syn_log,
            })
            prior_mtime = syn_mtime
            prior_ok = (syn_status == "SUCCESS")

            # PNR ladder
            for dir_name, disp in _PNR_LADDER:
                status, mt, log = _analyze_pnr_stage(
                    ws, dir_name, prior_mtime, prior_ok,
                )
                stages.append({
                    "name": disp, "status": status,
                    "mtime": _format_mtime(mt), "log": log,
                })
                if status == "SUCCESS":
                    prior_mtime = mt
                    prior_ok = True
                else:
                    prior_ok = False

            current_stage, current_status, last_completed = _derive_summary(stages)

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
            })
        except Exception as exc:
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False,
                    latency_s=time.time() - t0, round_num=-1,
                )
            return json.dumps({"ok": False, "error": "check failed: %s" % exc})

    return check_workspace_stage
