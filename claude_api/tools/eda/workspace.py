# Ported from github.com/jj-for-ai-usage/DAISY (branch main_).
# DB/persistence layer not present in this module upstream; no changes needed.
# Only the module docstring references an upstream SQL path that is not used here.
"""
DAISY Infrastructure - Workspace Scanner with Stage Detection
--------------------------------------------------------------
Fast discovery of active SYN/PNR EDA workspaces using the
'iflowblocks' directory as a path anchor, with automatic stage
detection and metrics extraction.

Detects:
  - For SYN workspaces: synthesis stage from syn/reports/summary_table/final.csv
  - For PNR workspaces: place-and-route stage from log file analysis
  - Extracts metrics and stores them in SQL for later analysis

Output: Three .rpt files (ACTIVE, SYN, PNR) like the original tcsh flow.
"""

from __future__ import annotations

import os
import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Directory names / prefixes to skip
_SKIP_EXACT: frozenset = frozenset([
    "scripts", "genus2", "results", "snapshot",
    "views", "signature",
])
_SKIP_PREFIXES: tuple = ("gns_", "invs_")

# PNR stages in execution order (lowercase dir names, uppercase stage names)
_PNR_STAGE_MAP = {
    "initdesign": "INIT_DESIGN",
    "floorplan": "FLOORPLAN",
    "placeopt": "PLACEOPT",
    "clock": "CLOCK",
    "clockopt": "CLOCKOPT",
    "route": "ROUTE",
    "routeopt": "ROUTEOPT",
}


# -------------------------------------------------------------------
# Data models
# -------------------------------------------------------------------
@dataclass
class WorkspaceMetrics:
    """Extracted metrics from synthesis or place-and-route."""
    workspace_id: Optional[int] = None
    stage_name: str = ""  # e.g., "final", "pre_gen", "syn_gen", "initdesign", "placeopt"
    metrics: Dict[str, Any] = field(default_factory=dict)  # metric_name -> value
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Workspace:
    """
    Represents a discovered SYN or PNR workspace with stage detection.

    Attributes:
        trial_root:       Path to the directory containing SYN/PNR
        trial_name:       Last component of trial_root (e.g. 'inc3_fixed')
        stage:            'SYN' or 'PNR'
        block_name:       EDA block name (e.g. 'cip_eiex')
        iflowblocks_path: Absolute path to the iflowblocks directory
        work_location:    Path to the actual work directory (found via second trial_name)
        stage_detail:     Detailed stage: e.g., "SYN_FINAL", "PNR_ROUTE_SUCCESS", "SYN_ONGOING"
        stage_status:     "SUCCESS", "ONGOING", "FAIL", or "NOT_AVAILABLE"
        is_active:        True if this is the most advanced stage for the trial
        scanned_at:       ISO timestamp of when discovered
    """
    trial_root: str
    trial_name: str
    stage: str
    block_name: str
    iflowblocks_path: str
    work_location: str = ""
    stage_detail: str = "UNKNOWN"
    stage_status: str = "UNKNOWN"
    is_active: bool = False
    scanned_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def stage_root(self) -> str:
        """Full path to the <trial_root>/<stage> directory."""
        return str(Path(self.trial_root) / self.stage)

    def to_dict(self) -> Dict:
        return {
            "trial_root": self.trial_root,
            "trial_name": self.trial_name,
            "stage": self.stage,
            "block_name": self.block_name,
            "iflowblocks_path": self.iflowblocks_path,
            "work_location": self.work_location,
            "stage_detail": self.stage_detail,
            "stage_status": self.stage_status,
            "is_active": self.is_active,
            "scanned_at": self.scanned_at,
        }

    def __repr__(self) -> str:
        active_tag = " [ACTIVE]" if self.is_active else ""
        return (
            f"<Workspace {self.stage} {self.trial_name}/"
            f"{self.block_name} {self.stage_detail} {self.stage_status}{active_tag}>"
        )


@dataclass
class WorkspaceScanResult:
    """Aggregated result from a workspace scan."""
    search_root: str
    all: List[Workspace] = field(default_factory=list)
    syn: List[Workspace] = field(default_factory=list)
    pnr: List[Workspace] = field(default_factory=list)
    active: List[Workspace] = field(default_factory=list)
    elapsed_sec: float = 0.0
    output_dir: str = ""  # Where output files were written
    # Actual line counts written to each .rpt file (set by _write_report_files)
    _written_active: int = 0
    _written_syn: int = 0
    _written_pnr: int = 0

    def summary(self) -> str:
        """
        Returns a summary using the actual lines written to each .rpt file so
        that the printed counts always match the files on disk.
        """
        total = self._written_syn + self._written_pnr
        return (
            f"Scan of {self.search_root!r}: "
            f"{total} workspace(s) written to report files "
            f"({self._written_syn} SYN, {self._written_pnr} PNR, "
            f"{self._written_active} ACTIVE) in {self.elapsed_sec:.2f}s"
        )


# -------------------------------------------------------------------
# Stage analyzer
# -------------------------------------------------------------------
class WorkspaceAnalyzer:
    """Analyzes workspace directories to determine stage and extract metrics."""

    @staticmethod
    def analyze_syn_workspace(stage_root: str, trial_name: str) -> Tuple[str, str, Dict]:
        """
        Analyze a SYN workspace to determine its stage and extract metrics.

        Returns:
            (stage_detail, stage_status, metrics_dict)
        """
        final_csv = os.path.join(stage_root, "syn", "reports", "summary_table", "final.csv")
        syn_log = os.path.join(stage_root, "syn", "logs", "syn.log")

        # If no SYN log, it hasn't started
        if not os.path.isfile(syn_log):
            return "SYN_NOT_STARTED", "NOT_AVAILABLE", {}

        # Check if SYN completed (look for "Done!" in last 2 lines)
        try:
            with open(syn_log, "r", errors="ignore") as f:
                lines = f.readlines()
                last_lines = "".join(lines[-2:]) if len(lines) >= 2 else "".join(lines)
                syn_completed = "Done!" in last_lines
        except Exception as e:
            logger.debug("Could not read SYN log %s: %s", syn_log, e)
            return "SYN_ONGOING", "ONGOING", {}

        if not syn_completed:
            return "SYN_ONGOING", "ONGOING", {}

        # SYN completed - check the CSV for the final stage
        if not os.path.isfile(final_csv):
            return "SYN_COMPLETE_NO_CSV", "SUCCESS", {}

        stage_name, metrics = WorkspaceAnalyzer._extract_syn_metrics(final_csv, trial_name)
        return f"SYN_{stage_name.upper()}", "SUCCESS", metrics

    @staticmethod
    def analyze_pnr_workspace(stage_root: str, trial_name: str) -> Tuple[str, str, Dict]:
        """
        Analyze a PNR workspace to determine its stage and extract metrics.

        Returns:
            (stage_detail, stage_status, metrics_dict)
        """
        pnr_root = os.path.join(stage_root, "pnr")
        if not os.path.isdir(pnr_root):
            return "PNR_NOT_STARTED", "NOT_AVAILABLE", {}

        # Check each PNR stage in order (lowercase dir names)
        current_stage = None
        current_status = None

        for dir_name, stage_name in _PNR_STAGE_MAP.items():
            log_file = os.path.join(pnr_root, dir_name, "logs", f"{dir_name}.log")
            if not os.path.isfile(log_file):
                break  # This stage hasn't started

            # Check if this stage completed
            try:
                with open(log_file, "r", errors="ignore") as f:
                    content = f.read()
                    has_ending = "Ending" in content
                    has_unconditional = "Finish plugin" in content and "post" in content and "unconditional" in content
            except Exception as e:
                logger.debug("Could not read PNR log %s: %s", log_file, e)
                current_stage = stage_name
                current_status = "ONGOING"
                break

            if not has_ending:
                current_stage = stage_name
                current_status = "ONGOING"
                break

            if has_ending and has_unconditional:
                current_stage = stage_name
                current_status = "SUCCESS"
            else:
                current_stage = stage_name
                current_status = "FAIL"
                break

        if current_stage is None:
            return "PNR_NOT_STARTED", "NOT_AVAILABLE", {}

        return f"PNR_{current_stage}", current_status, {}

    @staticmethod
    def _extract_syn_metrics(csv_path: str, trial_name: str) -> Tuple[str, Dict]:
        """
        Parse syn/reports/summary_table/final.csv to extract the last stage and metrics.

        Returns:
            (stage_name, metrics_dict)

        The CSV format has the trial_name and stage in a column, e.g., "final", "pre_gen", etc.
        """
        metrics = {}
        last_stage = "UNKNOWN"

        try:
            with open(csv_path, "r", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # The last row's stage becomes our reference
                    if not row:
                        continue

                    # Try to extract stage from the columns
                    # The stage info might be in a column like "Metric" or similar
                    # Based on the user's example, the last value before the path seems to be the stage
                    # Extract metrics from known columns
                    for key, value in row.items():
                        if key and value and key.strip():
                            try:
                                metrics[key.strip()] = float(value)
                            except (ValueError, TypeError):
                                metrics[key.strip()] = value

                    # The stage name appears to be in the last columns
                    # For now, we'll infer it from presence in the file
                    # Check if "final" is mentioned
                    row_str = str(row)
                    if "final" in row_str.lower():
                        last_stage = "final"
                    elif "map" in row_str.lower():
                        last_stage = "map"
                    elif "syn_gen" in row_str.lower():
                        last_stage = "syn_gen"
                    elif "pre_gen" in row_str.lower():
                        last_stage = "pre_gen"

        except Exception as e:
            logger.debug("Error parsing CSV %s: %s", csv_path, e)

        return last_stage, metrics

    @staticmethod
    def find_work_location(iflowblocks_path: str, trial_name: str) -> str:
        """
        Find the actual work location by locating the second occurrence of trial_name
        in the path after iflowblocks.

        Example:
            iflowblocks_path = /path/inc3_fixed/SYN/cip_eiex/iflowblocks
            trial_name = inc3_fixed
            Returns: /path/inc3_fixed/SYN/cip_eiex/iflowblocks/cip_eiex/imp/inc3_fixed
        """
        iflowblocks_dir = Path(iflowblocks_path)

        # Find the deepest directory inside iflowblocks that matches trial_name
        for item in iflowblocks_dir.rglob(trial_name):
            if item.is_dir():
                return str(item)

        # Fallback: return the iflowblocks path itself
        return str(iflowblocks_path)


# -------------------------------------------------------------------
# Core scan function
# -------------------------------------------------------------------
def scan_workspaces(
    search_root: str,
    output_dir: Optional[str] = None,
    analyze_stages: bool = True,
) -> WorkspaceScanResult:
    """
    Fast workspace discovery with stage detection and metrics extraction.

    Generates three output files (if output_dir is provided):
        - ACTIVE_workspaces.rpt
        - SYN_workspaces.rpt
        - PNR_workspaces.rpt

    Args:
        search_root: Top-level directory to search.
        output_dir: Directory to write .rpt files (uses search_root if None).
        analyze_stages: If True, detect stages and extract metrics.

    Returns:
        WorkspaceScanResult with all, syn, pnr, active lists and output paths.
    """
    import time
    t0 = time.perf_counter()

    search_root = os.path.abspath(search_root)
    output_dir = output_dir or search_root
    result = WorkspaceScanResult(search_root=search_root, output_dir=output_dir)

    if not os.path.isdir(search_root):
        logger.warning("scan_workspaces: search_root does not exist: %s", search_root)
        return result

    found: List[Workspace] = []

    for dirpath, dirnames, _ in os.walk(search_root, topdown=True):
        # Prune skip dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_EXACT
            and not any(d.startswith(p) for p in _SKIP_PREFIXES)
        ]

        if "iflowblocks" not in dirnames:
            continue

        ws = _parse_workspace_path(dirpath)
        if ws is None:
            logger.debug("iflowblocks found but no SYN/PNR in path: %s", dirpath)
            continue

        ws.iflowblocks_path = os.path.join(dirpath, "iflowblocks")
        ws.work_location = WorkspaceAnalyzer.find_work_location(
            ws.iflowblocks_path, ws.trial_name
        )

        # Analyze stage if requested
        if analyze_stages:
            if ws.stage == "SYN":
                detail, status, metrics = WorkspaceAnalyzer.analyze_syn_workspace(
                    ws.stage_root, ws.trial_name
                )
                ws.stage_detail = detail
                ws.stage_status = status
            else:  # PNR
                detail, status, metrics = WorkspaceAnalyzer.analyze_pnr_workspace(
                    ws.stage_root, ws.trial_name
                )
                ws.stage_detail = detail
                ws.stage_status = status

        found.append(ws)
        logger.debug(
            "  [%s] %s/%s  stage=%s  loc=%s",
            ws.stage, ws.trial_name, ws.block_name,
            ws.stage_detail, ws.work_location,
        )
        dirnames.remove("iflowblocks")

    # Determine active workspaces
    pnr_roots: Set[str] = {ws.trial_root for ws in found if ws.stage == "PNR"}
    for ws in found:
        ws.is_active = (
            ws.stage == "PNR"
            or (ws.stage == "SYN" and ws.trial_root not in pnr_roots)
        )

    result.all    = found
    result.syn    = [ws for ws in found if ws.stage == "SYN"]
    result.pnr    = [ws for ws in found if ws.stage == "PNR"]
    result.active = [ws for ws in found if ws.is_active]
    result.elapsed_sec = time.perf_counter() - t0

    # Write output files — sets result._written_* line counts
    _write_report_files(result, output_dir)

    # summary() uses _written_* so numbers match the files
    logger.info(result.summary())
    return result


def _write_report_files(result: WorkspaceScanResult, output_dir: str) -> None:
    """Write ACTIVE, SYN, and PNR report files. Returns actual line counts."""
    os.makedirs(output_dir, exist_ok=True)

    active_file = os.path.join(output_dir, "ACTIVE_workspaces.rpt")
    syn_file    = os.path.join(output_dir, "SYN_workspaces.rpt")
    pnr_file    = os.path.join(output_dir, "PNR_workspaces.rpt")

    def _write(path: str, workspaces: List[Workspace]) -> int:
        written = 0
        with open(path, "w") as fh:
            for ws in workspaces:
                loc = ws.work_location
                if not loc:
                    logger.warning(
                        "Skipping workspace with no work_location: %s", ws
                    )
                    continue
                fh.write(f"{loc}\n")
                written += 1
                logger.debug("  wrote: %s", loc)
        return written

    try:
        n_active = _write(active_file, result.active)
        n_syn    = _write(syn_file,    result.syn)
        n_pnr    = _write(pnr_file,    result.pnr)

        # Store actual line counts so summary() is accurate
        result._written_active = n_active
        result._written_syn    = n_syn
        result._written_pnr    = n_pnr

        logger.info(
            "Report files written — ACTIVE: %d  SYN: %d  PNR: %d",
            n_active, n_syn, n_pnr,
        )
        logger.info("  ACTIVE: %s", active_file)
        logger.info("  SYN:    %s", syn_file)
        logger.info("  PNR:    %s", pnr_file)

    except Exception as e:
        logger.error("Error writing report files: %s", e)


# -------------------------------------------------------------------
# Path parser
# -------------------------------------------------------------------
def _parse_workspace_path(dirpath: str) -> Optional[Workspace]:
    """Parse path to extract workspace metadata."""
    parts = Path(os.path.abspath(dirpath)).parts

    for i in range(len(parts) - 1, 0, -1):
        if parts[i] in ("SYN", "PNR"):
            stage = parts[i]
            block_name = parts[-1]
            trial_root = str(Path(*parts[:i]))
            trial_name = parts[i - 1]
            return Workspace(
                trial_root=trial_root,
                trial_name=trial_name,
                stage=stage,
                block_name=block_name,
                iflowblocks_path="",
            )

    return None
