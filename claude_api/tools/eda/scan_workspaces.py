"""Tool: scan_workspaces — discover active SYN/PNR EDA workspaces under a project root."""
from __future__ import annotations
import json
import os
import time

from . import workspace as _ws

NAME = "scan_workspaces"
DESCRIPTION = (
    "Walk an EDA project root and find all active Cadence SYN/PNR workspaces "
    "(directories containing 'iflowblocks'). Writes ACTIVE_workspaces.rpt, "
    "SYN_workspaces.rpt, and PNR_workspaces.rpt under output_dir (defaults to search_root). "
    "For each trial, PNR supersedes SYN in the ACTIVE list. "
    "Set analyze_stages=false to skip log-file stage detection for a faster scan."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "search_root": {
            "type": "string",
            "description": "Top-level directory to walk (e.g. /proj/vendor_*/EIEX_0.1). Must exist.",
        },
        "output_dir": {
            "type": "string",
            "description": "Directory to write the three .rpt files. Defaults to search_root.",
        },
        "analyze_stages": {
            "type": "boolean",
            "description": "Run stage detection (SUCCESS/ONGOING/FAIL) on each workspace. Default true.",
        },
    },
    "required": ["search_root"],
}


def make_handler(audit=None, **kwargs):
    def scan_workspaces(search_root: str,
                        output_dir: str = "",
                        analyze_stages: bool = True) -> str:
        t0 = time.time()
        search_root = os.path.abspath(search_root)

        if not os.path.isdir(search_root):
            elapsed = time.time() - t0
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False, latency_s=elapsed, round_num=-1,
                )
            return json.dumps({
                "ok": False,
                "error": "search_root does not exist or is not a directory: %s" % search_root,
            })

        out_dir = os.path.abspath(output_dir) if output_dir else search_root

        try:
            result = _ws.scan_workspaces(
                search_root=search_root,
                output_dir=out_dir,
                analyze_stages=analyze_stages,
            )
            elapsed = time.time() - t0
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=True, latency_s=elapsed, round_num=-1,
                )
            return json.dumps({
                "ok": True,
                "search_root": search_root,
                "output_dir": out_dir,
                "counts": {
                    "active": result._written_active,
                    "syn": result._written_syn,
                    "pnr": result._written_pnr,
                    "total": len(result.all),
                },
                "rpt_files": {
                    "active": os.path.join(out_dir, "ACTIVE_workspaces.rpt"),
                    "syn":    os.path.join(out_dir, "SYN_workspaces.rpt"),
                    "pnr":    os.path.join(out_dir, "PNR_workspaces.rpt"),
                },
                "elapsed_sec": round(elapsed, 3),
            })
        except Exception as exc:
            elapsed = time.time() - t0
            if audit is not None:
                audit.log_tool_execution(
                    tool_name=NAME, success=False, latency_s=elapsed, round_num=-1,
                )
            return json.dumps({"ok": False, "error": "scan failed: %s" % exc})

    return scan_workspaces
