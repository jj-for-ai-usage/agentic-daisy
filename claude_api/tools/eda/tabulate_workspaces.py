"""Tool: tabulate_workspaces — extract metrics from EDA workspaces into a semicolon CSV."""
from __future__ import annotations
import json
import logging
import os
import time
from typing import List, Optional, Tuple

from .tabulator import Tabulator

LOG = logging.getLogger("daisy.eda.tabulate_workspaces")

NAME = "tabulate_workspaces"
DESCRIPTION = (
    "Extract timing/power/area metrics from a set of Cadence SYN/PNR workspaces "
    "and emit a semicolon-delimited CSV (Excel-paste ready). "
    "Supply either workspace_list_file (path to an ACTIVE_workspaces.rpt-style file, "
    "one workspace path per line) OR work_dirs (inline list). "
    "Pass baseline=<workspace dir> to add PLACEOPT/CLOCKOPT/ROUTEOPT delta sections. "
    "Writes CSV to output_file and returns num_trials, extraction_errors_count "
    "(trials that failed metric extraction), preview_truncated, and a short preview."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace_list_file": {
            "type": "string",
            "description": "Path to .rpt file with one workspace directory per line. "
                           "Typically ACTIVE_workspaces.rpt from scan_workspaces. "
                           "Mutually exclusive with work_dirs.",
        },
        "work_dirs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Inline list of workspace directories. Mutually exclusive with workspace_list_file.",
        },
        "output_file": {
            "type": "string",
            "description": "Where to write the CSV. Defaults to <input_dir>/tabulation.csv.",
        },
        "baseline": {
            "type": "string",
            "description": "Optional workspace directory to use as the baseline trial. "
                           "Adds PLACEOPT/CLOCKOPT/ROUTEOPT delta sections comparing every "
                           "other trial against it.",
        },
    },
}

PREVIEW_LINES = 20
MAX_PREVIEW_CHARS = 4_000
_TRUNC_MARKER = "\n... (truncated)"


def _load_dirs_from_file(path: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Return (dirs, error_msg). error_msg is None on success."""
    try:
        with open(path, "r") as fh:
            return [ln.strip() for ln in fh if ln.strip()], None
    except OSError as exc:
        return None, "read failed: %s: %s" % (type(exc).__name__, exc)


def _count_extraction_errors(csv_text: str) -> int:
    """Count trials whose Block Name row value is 'ERROR'.

    The ported Tabulator marks a failed-extraction trial with block_name='ERROR'
    (it then emits the row normally through the Excel-safe prefix, producing
    cells like "' ERROR"). We scan only the Block Name header row.
    """
    for line in csv_text.splitlines():
        if line.startswith("Block Name;"):
            cells = line.split(";")[1:]  # skip the "Block Name" label itself
            return sum(1 for c in cells if c.strip().lstrip("'").strip() == "ERROR")
    return 0


def make_handler(audit=None, **kwargs):
    def tabulate_workspaces(workspace_list_file: str = "",
                            work_dirs=None,
                            output_file: str = "",
                            baseline: str = "") -> str:
        t0 = time.time()
        work_dirs = work_dirs or []

        def _audit_and_return(payload: dict, success: bool) -> str:
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=success,
                    latency_s=time.time() - t0, round_num=-1,
                )
            return json.dumps(payload)

        # --- Resolve input: exactly one of workspace_list_file / work_dirs ---
        if workspace_list_file and work_dirs:
            return _audit_and_return({
                "ok": False,
                "error": "Provide either workspace_list_file or work_dirs, not both.",
                "error_code": "INVALID_INPUT",
            }, success=False)
        if not workspace_list_file and not work_dirs:
            return _audit_and_return({
                "ok": False,
                "error": "Must provide workspace_list_file or work_dirs.",
                "error_code": "INVALID_INPUT",
            }, success=False)

        source_label = ""
        try:
            if workspace_list_file:
                workspace_list_file = os.path.abspath(workspace_list_file)
                if not os.path.isfile(workspace_list_file):
                    return _audit_and_return({
                        "ok": False,
                        "error": "workspace_list_file not found: %s" % workspace_list_file,
                        "error_code": "FILE_NOT_FOUND",
                    }, success=False)
                loaded, load_err = _load_dirs_from_file(workspace_list_file)
                if load_err is not None:
                    return _audit_and_return({
                        "ok": False,
                        "error": "could not read %s: %s" % (workspace_list_file, load_err),
                        "error_code": "FILE_READ_ERROR",
                    }, success=False)
                work_dirs = loaded
                source_label = workspace_list_file
                default_out_dir = os.path.dirname(workspace_list_file)
            else:
                source_label = "inline (%d dirs)" % len(work_dirs)
                default_out_dir = os.path.abspath(work_dirs[0]) if work_dirs else os.getcwd()
                if not work_dirs or not os.path.isdir(default_out_dir):
                    default_out_dir = os.getcwd()

            out = os.path.abspath(output_file) if output_file else os.path.join(
                default_out_dir, "tabulation.csv"
            )

            if not work_dirs:
                # Empty list — write an empty file so downstream steps don't 404
                os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                with open(out, "w") as fh:
                    fh.write("")
                return _audit_and_return({
                    "ok": True,
                    "output_file": out,
                    "source": source_label,
                    "num_trials": 0,
                    "num_rows": 0,
                    "extraction_errors_count": 0,
                    "baseline_applied": False,
                    "preview": "",
                    "preview_truncated": False,
                }, success=True)

            csv_text = Tabulator().tabulate_to_string(
                work_dirs, baseline_dir=baseline or None,
            )
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            with open(out, "w") as fh:
                fh.write(csv_text)

            lines = csv_text.splitlines()
            preview = "\n".join(lines[:PREVIEW_LINES])
            preview_truncated = False
            if len(preview) > MAX_PREVIEW_CHARS:
                keep = max(0, MAX_PREVIEW_CHARS - len(_TRUNC_MARKER))
                preview = preview[:keep] + _TRUNC_MARKER
                preview_truncated = True

            extraction_errors = _count_extraction_errors(csv_text)

            return _audit_and_return({
                "ok": True,
                "output_file": out,
                "source": source_label,
                "num_trials": len(work_dirs),
                "num_rows": len(lines),
                "extraction_errors_count": extraction_errors,
                "baseline_applied": bool(baseline),
                "preview": preview,
                "preview_truncated": preview_truncated,
            }, success=True)
        except Exception as exc:
            return _audit_and_return({
                "ok": False,
                "error": "tabulate failed: %s: %s" % (type(exc).__name__, exc),
                "error_code": "UNEXPECTED_ERROR",
                "error_type": type(exc).__name__,
            }, success=False)

    return tabulate_workspaces
