"""Tool: tabulate_workspaces — extract metrics from EDA workspaces into a semicolon CSV."""
from __future__ import annotations
import json
import os
import time

from .tabulator import Tabulator

NAME = "tabulate_workspaces"
DESCRIPTION = (
    "Extract timing/power/area metrics from a set of Cadence SYN/PNR workspaces "
    "and emit a semicolon-delimited CSV (Excel-paste ready). "
    "Supply either workspace_list_file (path to an ACTIVE_workspaces.rpt-style file, "
    "one workspace path per line) OR work_dirs (inline list). "
    "Pass baseline=<workspace dir> to add PLACEOPT/CLOCKOPT/ROUTEOPT delta sections. "
    "Writes CSV to output_file and returns counts plus a short preview."
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


def _load_dirs_from_file(path: str):
    with open(path, "r") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def make_handler(audit=None, **kwargs):
    def tabulate_workspaces(workspace_list_file: str = "",
                            work_dirs=None,
                            output_file: str = "",
                            baseline: str = "") -> str:
        t0 = time.time()
        work_dirs = work_dirs or []

        # --- Resolve input: exactly one of workspace_list_file / work_dirs ---
        if workspace_list_file and work_dirs:
            return json.dumps({
                "ok": False,
                "error": "Provide either workspace_list_file or work_dirs, not both.",
            })
        if not workspace_list_file and not work_dirs:
            return json.dumps({
                "ok": False,
                "error": "Must provide workspace_list_file or work_dirs.",
            })

        source_label = ""
        try:
            if workspace_list_file:
                workspace_list_file = os.path.abspath(workspace_list_file)
                if not os.path.isfile(workspace_list_file):
                    return json.dumps({
                        "ok": False,
                        "error": "workspace_list_file not found: %s" % workspace_list_file,
                    })
                work_dirs = _load_dirs_from_file(workspace_list_file)
                source_label = workspace_list_file
                default_out_dir = os.path.dirname(workspace_list_file)
            else:
                source_label = "inline (%d dirs)" % len(work_dirs)
                default_out_dir = os.path.abspath(work_dirs[0]) if work_dirs else os.getcwd()
                if work_dirs and os.path.isdir(default_out_dir):
                    pass
                else:
                    default_out_dir = os.getcwd()

            out = os.path.abspath(output_file) if output_file else os.path.join(
                default_out_dir, "tabulation.csv"
            )

            if not work_dirs:
                # Empty list — write an empty file so downstream steps don't 404
                os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                with open(out, "w") as fh:
                    fh.write("")
                elapsed = time.time() - t0
                if audit is not None:
                    audit.log_tool_execution(
                        tool_name=NAME, success=True, latency_s=elapsed, round_num=-1,
                    )
                return json.dumps({
                    "ok": True,
                    "output_file": out,
                    "source": source_label,
                    "num_trials": 0,
                    "num_rows": 0,
                    "baseline_applied": False,
                    "preview": "",
                })

            csv_text = Tabulator().tabulate_to_string(
                work_dirs, baseline_dir=baseline or None,
            )
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            with open(out, "w") as fh:
                fh.write(csv_text)

            lines = csv_text.splitlines()
            preview = "\n".join(lines[:PREVIEW_LINES])
            if len(preview) > MAX_PREVIEW_CHARS:
                preview = preview[:MAX_PREVIEW_CHARS] + "\n... (truncated)"

            elapsed = time.time() - t0
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=True, latency_s=elapsed, round_num=-1,
                )
            return json.dumps({
                "ok": True,
                "output_file": out,
                "source": source_label,
                "num_trials": len(work_dirs),
                "num_rows": len(lines),
                "baseline_applied": bool(baseline),
                "preview": preview,
            })
        except Exception as exc:
            elapsed = time.time() - t0
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False, latency_s=elapsed, round_num=-1,
                )
            return json.dumps({"ok": False, "error": "tabulate failed: %s" % exc})

    return tabulate_workspaces
