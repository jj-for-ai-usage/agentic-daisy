# Ported from github.com/jj-for-ai-usage/DAISY (branch main_).
# This module is already stdlib-only and carries no DB/persistence layer upstream.
"""
DAISY Reports - Tabulator
--------------------------
Produces semicolon-delimited, XLS-paste-ready reports from a list of
work directories (as output by the workspace scanner).

Each trial becomes one column. Rows group metrics by synthesis stage,
then by PNR stage. The format exactly mirrors the legacy tcsh tab scripts.

Output structure:
  Title            ; trial_A        ; trial_B
  Block Name       ; cip_eiex       ; cip_eiex
  Version          ; 25.71-e050_1   ; 25.71-e051_1
  Metric           ; constraints    ; constraints
  Slack(ns)        ; -0.511         ; -0.511
  ...
  Metric           ; pre_gen        ; pre_gen
  ...
  Stage            ; PLACEOPT       ; PLACEOPT
  ...

Usage:
    from DAISY.reports import Tabulator

    tab = Tabulator()
    tab.tabulate_from_file("ACTIVE_workspaces.rpt", "table.csv")

    # or from a list directly:
    tab.tabulate(["/path/to/ws1", "/path/to/ws2"], "table.csv")
"""

from __future__ import annotations

import csv
import glob
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NA = ""   # Missing / not-available — output as blank so XLS cells stay empty

# ---------------------------------------------------------------------------
# SYN CSV column map — positional because the header has duplicate names
# (R2R/I2R/R2O/I2O/CG appear twice: once for WNS sub-paths, once for TNS)
# ---------------------------------------------------------------------------
# WNS/TNS column pairs — each rendered as a single "wns / tns" cell.
# Each entry: (wns_col_index, tns_col_index, row_label)
_SYN_WNS_TNS_PAIRS: List[Tuple[int, int, str]] = [
    (1,  7,  "Slack"),   # Overall WNS (col 1) / TNS (col 7)
    (2,  8,  "R2R"),     # Reg-to-Reg
    (3,  9,  "I2R"),     # In-to-Reg
    (4,  10, "R2O"),     # Reg-to-Out
    (5,  11, "I2O"),     # In-to-Out
    (6,  12, "CG"),      # Clock-Gating
]

# Standalone metric columns — single value per cell.
# CellArea (17), LeafInstances (19), RouteOverflowH (24), RouteOverflowV (25),
# MBCI (26), MaxCong (27), TotCong (28) removed per spec.
_SYN_SOLO_COLS: List[Tuple[int, str]] = [
    (13, "FailingPaths"),
    (14, "LeakagePower(mW)"),
    (15, "DynamicPower(mW)"),
    # 16 = Clk Tree Power — omitted
    # 17 = CellArea — removed per spec
    (18, "TotalCellArea"),
    # 19 = LeafInstances — removed per spec
    (20, "TotalInstances"),
    (21, "Utilization(%)"),
    # 22 = Tot. Net Length — omitted
    # 23 = Avg. Net Length — omitted
    # 24 = RouteOverflowH — removed per spec
    # 25 = RouteOverflowV — removed per spec
    # 26 = MBCI — removed per spec
    # 27 = MaxCong — removed per spec
    # 28 = TotCong — removed per spec
    # 29 = CPU Runtime — omitted
    # 31 = CPU Elapsed — omitted
    # 33 = Memory — omitted
]

# Duration columns — collected and emitted as a separate Durations section.
_SYN_DURATION_COLS: List[Tuple[int, str]] = [
    (30, "RealRuntime"),
    (32, "RealElapsed"),
]

_SYN_VERSION_COL = 34   # e.g. "25.71-e050_1"
_SYN_MCPS_COL    = 35   # e.g. "24"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class SynStageData:
    """
    Metrics for one synthesis stage (one row in final.csv).

    metrics is an ORDERED LIST of (label, value) pairs — not a dict —
    because duplicate labels exist (R2R appears for both WNS and TNS).
    """
    stage_name: str
    metrics: Dict[int, str] = field(default_factory=dict)          # {col_idx: value}
    _meta: Dict[str, str] = field(default_factory=dict)            # _version, _mcps


@dataclass
class PnrStageData:
    """
    Metrics for one PNR stage (PLACEOPT, CLOCKOPT, ROUTE, ROUTEOPT, etc.).

    Each stage may have timing and power data extracted from its log.
    power_reports maps source type ("innovus", "xreplay", "multi_vcd_xreplay")
    to the corresponding PnrPowerData.
    """
    stage_name: str
    logfile_path: str = ""
    timing: Optional[PnrTimingData] = None
    power_reports: Dict[str, PnrPowerData] = field(default_factory=dict)
    metrics: Dict[str, str] = field(default_factory=dict)


@dataclass
class CellDepthData:
    """
    Parsed cell depth analysis from syn/reports/cell_depths.<stage>.rpt.

    buckets maps "lo to hi" -> (all_count, total_count) strings.
    bucket_order preserves the order they appear in the file so the union
    across trials can be sorted numerically.
    """
    stage_name: str
    max_depth: str = ""
    buckets: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    bucket_order: List[str] = field(default_factory=list)
    total_cells: str = ""
    total_cells_gt25: str = ""
    pct_gt25: str = ""
    mean: str = ""
    std_dev: str = ""


@dataclass
class XReplayData:
    """
    Parsed XReplay power data from syn/results/xreplay/XREPLAY_ON_<stage>.log.

    Basic format (8 fields):   Cells Pct Leakage Internal Switching Total Lvl Instance
    Extended format (11 fields): adds TGInternal TGSwitching IGInternal before Total.
    """
    stage_name: str
    leakage: str = ""
    internal: str = ""
    switching: str = ""
    glitch: str = ""
    total: str = ""
    tg_internal: str = ""
    tg_switching: str = ""
    ig_internal: str = ""


@dataclass
class GateStatisticsData:
    """
    Parsed gate statistics from syn/reports/map/map_gates.rpt.

    Extracts counts of cells by type, complexity, and drive strength.
    Each category (cell_types, complexity, drive_strength) maps category_name -> count_string.
    A single cell may match multiple patterns across different categories.
    """
    cell_types: Dict[str, str] = field(default_factory=dict)
    cell_types_order: List[str] = field(default_factory=list)  # Canonical order
    complexity: Dict[str, str] = field(default_factory=dict)
    complexity_order: List[str] = field(default_factory=list)
    drive_strength: Dict[str, str] = field(default_factory=dict)
    drive_strength_order: List[str] = field(default_factory=list)
    total_instances: str = ""  # Count of unique cells


@dataclass
class PnrTimingData:
    """
    Parsed timing metrics from last report_timing in a PNR stage log.

    path_groups maps full path group name -> (WNS_ns, TNS_ns, FailingPaths) tuple.
    Condensed names like "1:Fro..ro" in the header are expanded using
    the Path Group Name table found in the same log section.
    """
    stage_name: str
    corner_name: str = ""           # e.g., "Functional.emaxeovh_socov"
    density: str = ""               # e.g., "59.532%"
    # name -> (WNS, TNS, FailingPaths)
    path_groups: Dict[str, Tuple[str, str, str]] = field(default_factory=dict)
    hepg_wns: str = ""              # HEPG weighted WNS (ns)
    hepg_tns: str = ""              # HEPG weighted TNS (ns)
    all_paths_wns: str = ""         # All Paths WNS (ns)
    all_paths_tns: str = ""         # All Paths TNS (ns)
    all_paths_fp: str = ""          # All Paths failing path count
    mbff: str = ""                  # MBFF value
    drv: str = ""                   # DRV (cap/tran) row data


@dataclass
class PnrPowerData:
    """
    Parsed power metrics from last report_power in a PNR stage log.

    power_source indicates where the power numbers come from:
        "innovus"           - Activity annotation < 100%
        "xreplay"           - Activity annotation = 100% from *opt.log
        "multi_vcd_xreplay" - Activity annotation = 100% from *-export-default.log

    groups maps group name (Sequential, Macro, IO, Combinational, etc.)
    to a dict with keys: internal, switching, leakage, total, percentage.
    """
    stage_name: str
    power_source: str = ""          # "innovus", "xreplay", "multi_vcd_xreplay"
    total_internal: str = ""        # mW
    total_switching: str = ""       # mW
    total_leakage: str = ""         # mW
    total_power: str = ""           # mW
    groups: Dict[str, Dict[str, str]] = field(
        default_factory=dict
    )  # group_name -> {internal, switching, leakage, total, percentage}


@dataclass
class TrialData:
    """All tabulation data extracted from one work directory."""
    work_dir: str
    trial_name: str = ""
    block_name: str = ""
    version: str = ""           # Synthesis tool version from CSV col 34
    mcps: str = ""              # Machine cores from CSV col 35 (not output)
    innovus_version: str = ""   # PNR tool version (extracted from PNR log — stub)
    syn_log_path: str = ""      # Path used for the PATH footer row
    syn_stages: List[SynStageData] = field(default_factory=list)
    pnr_stages: List[PnrStageData] = field(default_factory=list)
    os_info: str = ""           # retained but not output
    machine: str = ""           # e.g. "32cores_64cpus_AMD"
    ple_used: str = ""          # retained but not output
    wcmfsp: str = ""            # retained but not output
    # SYN supplemental metrics
    cell_depths: Dict[str, CellDepthData] = field(default_factory=dict)
    cell_depths_order: List[str] = field(default_factory=list)   # stage names by mtime
    xreplay_power: Dict[str, XReplayData] = field(default_factory=dict)
    xreplay_order: List[str] = field(default_factory=list)       # stage names by mtime
    gate_statistics: Optional[GateStatisticsData] = None          # from syn/reports/map/map_gates.rpt
    wirelength: str = ""        # from [NR-eGR] Total length: in syn.log (um)
    via_count: str = ""         # from [NR-eGR] number of vias: in syn.log


# ---------------------------------------------------------------------------
# Core Tabulator class
# ---------------------------------------------------------------------------
class Tabulator:
    """
    Converts a list of EDA work directories into a semicolon-delimited table
    suitable for copy-paste into Excel / LibreOffice Calc.

    Each column is one trial; each row is one metric label.
    Metric sections are grouped by synthesis stage then PNR stage.

    A row is suppressed when ALL trials show the metric as "no_value" —
    this keeps early-stage sections clean (e.g. LeakagePower not shown
    for the 'constraints' stage because routing hasn't happened yet).

    Methods ending in _stub are placeholders where the user will supply
    the real extraction logic in a follow-up request.
    """

    SEPARATOR = ";"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def tabulate_from_file(
        self, workspace_list_file: str, output_file: str
    ) -> None:
        """
        Load work directories from a .rpt file (one path per line) and
        write the tabulation to output_file.
        """
        with open(workspace_list_file, "r") as f:
            work_dirs = [line.strip() for line in f if line.strip()]
        self.tabulate(work_dirs, output_file)

    def tabulate(self, work_dirs: List[str], output_file: str) -> None:
        """
        Tabulate a list of work directories and write semicolon CSV to output_file.
        """
        result = self.tabulate_to_string(work_dirs)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w") as f:
            f.write(result)
        logger.info("Tabulation written to %s (%d trials)", output_file, len(work_dirs))

    def tabulate_to_string(
        self, work_dirs: List[str], baseline_dir: Optional[str] = None
    ) -> str:
        """
        Tabulate a list of work directories and return the result as a string.
        Useful for testing or embedding in other reports.

        If baseline_dir is supplied it is placed as the first column and
        three PNR comparison sections (PLACEOPT/CLOCKOPT/ROUTEOPT) are
        appended that show delta vs baseline for each trial.
        """
        trials = []
        for wd in work_dirs:
            try:
                trials.append(self._extract_trial(wd))
            except Exception as exc:
                logger.error("Could not extract data from %s: %s", wd, exc)
                trials.append(TrialData(
                    work_dir=wd,
                    trial_name=os.path.basename(wd),
                    block_name="ERROR",
                ))

        baseline_idx: Optional[int] = None

        if baseline_dir:
            baseline_abs = os.path.abspath(baseline_dir)
            # Find if baseline is already in the list
            for i, t in enumerate(trials):
                if os.path.abspath(t.work_dir) == baseline_abs:
                    baseline_idx = i
                    break

            if baseline_idx is None:
                # Not in list — extract and prepend
                try:
                    bl_trial = self._extract_trial(baseline_dir)
                    trials.insert(0, bl_trial)
                    baseline_idx = 0
                    logger.info("Baseline trial extracted from: %s", baseline_dir)
                except Exception as exc:
                    logger.error("Could not extract baseline %s: %s", baseline_dir, exc)
                    baseline_idx = None

            if baseline_idx is not None:
                # Move baseline to front; sort the rest alphabetically
                bl = trials.pop(baseline_idx)
                trials.sort(key=lambda t: t.trial_name.lower())
                trials.insert(0, bl)
                baseline_idx = 0
                logger.info("Baseline: %s (column 0)", bl.trial_name)
            else:
                trials.sort(key=lambda t: t.trial_name.lower())
        else:
            trials.sort(key=lambda t: t.trial_name.lower())

        rows = self._build_rows(trials, baseline_idx=baseline_idx)
        sep = self.SEPARATOR
        # Prefix every non-empty data cell (col 1+) with a single quote so that
        # Excel / LibreOffice Calc forces text mode and never interprets values
        # as formulas, dates, or division operations.
        def _xls_text(v: str) -> str:
            return ("' " + v) if v else v

        lines = [sep.join([row[0]] + [_xls_text(v) for v in row[1:]]) for row in rows]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    def _extract_trial(self, work_dir: str) -> TrialData:
        """
        Main entry point: extract all available metrics from one work directory.
        """
        work_dir = os.path.abspath(work_dir)
        trial = TrialData(work_dir=work_dir)

        # --- Derive trial_name, block_name, stage from path ---
        trial.trial_name, trial.block_name, stage = _parse_path_info(work_dir)
        logger.info("Extracting trial: %s  [%s/%s]", work_dir, trial.trial_name, trial.block_name)

        # --- SYN data ---
        csv_path = _find_syn_csv(work_dir)
        if csv_path:
            logger.debug("  SYN CSV: %s", csv_path)
            trial.syn_stages = self._parse_syn_csv(csv_path)
            trial.syn_log_path = os.path.join(work_dir, "syn", "logs", "syn.log")
            # Pull version + mcps from first stage row that has them
            for s in trial.syn_stages:
                v = s._meta.get("_version", "")
                m = s._meta.get("_mcps", "")
                if v:
                    trial.version = v
                if m:
                    trial.mcps = m
                if v and m:
                    break
            logger.info(
                "  SYN: %d stage(s)  version=%s",
                len(trial.syn_stages), trial.version or "-not available-",
            )
        else:
            logger.debug("  SYN CSV: not found under %s", work_dir)

        # --- SYN supplemental data ---
        trial.cell_depths        = self._extract_cell_depths(work_dir)
        trial.cell_depths_order  = list(trial.cell_depths.keys())   # mtime order
        trial.xreplay_power      = self._extract_xreplay_power(work_dir)
        trial.xreplay_order      = list(trial.xreplay_power.keys()) # mtime order
        trial.wirelength, trial.via_count = self._extract_syn_wirelength(work_dir)
        trial.gate_statistics    = self._extract_gate_statistics(work_dir)

        # --- PNR data ---
        if stage == "PNR":
            trial.pnr_stages = self._extract_pnr_stages_stub(work_dir)
            logger.info("  PNR: %d stage(s)", len(trial.pnr_stages))

        # --- Additional header/footer data stubs ---
        trial.innovus_version = self._extract_innovus_version_stub(work_dir)
        trial.os_info         = self._extract_os_stub(work_dir)
        trial.machine         = self._extract_machine_stub(work_dir)
        trial.ple_used        = self._extract_ple_stub(work_dir)
        trial.wcmfsp          = self._extract_wcmfsp_stub(work_dir)

        return trial

    def _parse_syn_csv(self, csv_path: str) -> List[SynStageData]:
        """
        Parse syn/reports/*/final.csv positionally (column names are not
        unique — R2R, I2R, etc. appear both for WNS and TNS sub-categories).

        Each CSV row is one synthesis stage.  The stage name is in column 0.
        Version (col 34) and MCPS (col 35) are stored as private keys
        '_version' and '_mcps' and used only for header rows.

        Returns an ordered list of SynStageData, one per stage.
        """
        stages: List[SynStageData] = []
        try:
            with open(csv_path, "r", errors="ignore") as f:
                reader = csv.reader(f)
                header_skipped = False
                for raw_row in reader:
                    if not raw_row:
                        continue
                    # Skip the header row (first column is "Metric")
                    if not header_skipped:
                        header_skipped = True
                        if raw_row[0].strip().lower() == "metric":
                            continue
                    stage_name = raw_row[0].strip()
                    if not stage_name:
                        continue
                    sd = SynStageData(stage_name=stage_name)
                    # Collect all column indices we care about
                    _all_col_indices = set(
                        col
                        for pair in _SYN_WNS_TNS_PAIRS for col in (pair[0], pair[1])
                    ) | set(
                        col for col, _ in _SYN_SOLO_COLS
                    ) | set(
                        col for col, _ in _SYN_DURATION_COLS
                    )
                    for col_idx in _all_col_indices:
                        sd.metrics[col_idx] = (
                            raw_row[col_idx].strip()
                            if col_idx < len(raw_row)
                            else "no_value"
                        )
                    # Version and MCPS stored separately (not output as rows)
                    if _SYN_VERSION_COL < len(raw_row):
                        sd._meta["_version"] = raw_row[_SYN_VERSION_COL].strip()
                    if _SYN_MCPS_COL < len(raw_row):
                        sd._meta["_mcps"] = raw_row[_SYN_MCPS_COL].strip()
                    stages.append(sd)
                    logger.debug(
                        "    SYN stage %-20s  Slack=%s  TNS=%s  LeakPwr=%s  TotalCellArea=%s",
                        stage_name,
                        sd.metrics.get(1, "-"),   # col 1 = Slack WNS
                        sd.metrics.get(7, "-"),   # col 7 = TNS
                        sd.metrics.get(14, "-"),  # col 14 = LeakagePower
                        sd.metrics.get(18, "-"),  # col 18 = TotalCellArea
                    )
        except Exception as e:
            logger.error("Error parsing SYN CSV %s: %s", csv_path, e)
        return stages

    # ------------------------------------------------------------------
    # SYN supplemental extraction
    # ------------------------------------------------------------------
    def _extract_cell_depths(self, work_dir: str) -> Dict[str, CellDepthData]:
        """
        Parse all syn/reports/cell_depths.<stage>.rpt files.

        Returns a dict mapping stage_name -> CellDepthData.
        Stage name is derived from the filename, e.g.:
            cell_depths.post_syn_gen.rpt  ->  "post_syn_gen"
        """
        result: Dict[str, CellDepthData] = {}
        pattern = os.path.join(work_dir, "syn", "reports", "cell_depths.*.rpt")
        # Sort by modification time so the dict preserves the actual run order
        matches = sorted(glob.glob(pattern), key=os.path.getmtime)
        logger.debug("  Cell depth files found: %d", len(matches))
        for rpt_path in matches:
            basename = os.path.basename(rpt_path)
            stage_name = basename[len("cell_depths."):-len(".rpt")]
            data = self._parse_cell_depth_file(rpt_path, stage_name)
            if data is not None:
                result[stage_name] = data
                logger.debug(
                    "    cell_depths %-20s  MaxDepth=%-4s  Buckets=%d  Mean=%s  StdDev=%s",
                    stage_name, data.max_depth, len(data.buckets),
                    data.mean, data.std_dev,
                )
            else:
                logger.debug("    cell_depths %-20s  PARSE FAILED", stage_name)
        return result

    def _parse_cell_depth_file(
        self, rpt_path: str, stage_name: str
    ) -> Optional[CellDepthData]:
        """
        Parse one cell_depths.*.rpt file.

        Expected format (whitespace-aligned fixed-width):
            Generating path depth analysis for design: <block>
                                   All     Total
            Max Depth :            44
            1 to 5 :            10938    744021
            6 to 10 :          106383    733083
            ...
            Total Cells :                744021
            Total Cells  > 25 :           98905
            PCT of Cells > 25 :            13.3%
            Mean :                         16.1
            Std Dev:                       6.98
        """
        data = CellDepthData(stage_name=stage_name)

        bucket_re    = re.compile(r'^\s*(\d+)\s+to\s+(\d+)\s*:\s+([\d,]+)\s+([\d,]+)')
        max_depth_re = re.compile(r'^\s*Max\s+Depth\s*:\s*([\d]+)')
        # Check GT25 before plain "Total Cells" to avoid partial match
        total_gt25_re   = re.compile(r'^\s*Total\s+Cells\s+>\s*25\s*:\s*([\d,]+)')
        total_cells_re  = re.compile(r'^\s*Total\s+Cells\s*:\s*([\d,]+)')
        pct_gt25_re     = re.compile(r'^\s*PCT\s+of\s+Cells\s*>\s*25\s*:\s*([\d.%]+)')
        mean_re         = re.compile(r'^\s*Mean\s*:\s*([\d.]+)')
        std_dev_re      = re.compile(r'^\s*Std\s+Dev\s*:\s*([\d.]+)')

        try:
            with open(rpt_path, "r", errors="ignore") as f:
                for line in f:
                    m = bucket_re.match(line)
                    if m:
                        lo, hi, all_count, total_count = m.groups()
                        label = f"{lo} to {hi}"
                        data.buckets[label] = (all_count, total_count)
                        data.bucket_order.append(label)
                        continue
                    m = max_depth_re.match(line)
                    if m:
                        data.max_depth = m.group(1)
                        continue
                    m = total_gt25_re.match(line)
                    if m:
                        data.total_cells_gt25 = m.group(1)
                        continue
                    m = total_cells_re.match(line)
                    if m:
                        data.total_cells = m.group(1)
                        continue
                    m = pct_gt25_re.match(line)
                    if m:
                        data.pct_gt25 = m.group(1)
                        continue
                    m = mean_re.match(line)
                    if m:
                        data.mean = m.group(1)
                        continue
                    m = std_dev_re.match(line)
                    if m:
                        data.std_dev = m.group(1)
        except Exception as exc:
            logger.error("Error parsing cell depth file %s: %s", rpt_path, exc)
            return None

        return data

    def _extract_xreplay_power(self, work_dir: str) -> Dict[str, XReplayData]:
        """
        Parse all syn/results/xreplay/XREPLAY_ON_<stage>.log files.

        Greps for the level-0 power row (Lvl == "0") and extracts power
        components.  Stage name derived from filename:
            XREPLAY_ON_post_syn_gen.log  ->  "post_syn_gen"
        """
        result: Dict[str, XReplayData] = {}
        pattern = os.path.join(
            work_dir, "syn", "results", "xreplay", "XREPLAY_ON_*.log"
        )
        # Sort by modification time to match the actual synthesis run order
        matches = sorted(glob.glob(pattern), key=os.path.getmtime)
        logger.debug("  XReplay log files found: %d", len(matches))
        for log_path in matches:
            basename = os.path.basename(log_path)
            stage_name = basename[len("XREPLAY_ON_"):-len(".log")]
            data = self._parse_xreplay_log(log_path, stage_name)
            if data is not None:
                result[stage_name] = data
                logger.debug(
                    "    xreplay %-20s  Leak=%s  Int=%s  Sw=%s  Tot=%s",
                    stage_name, data.leakage, data.internal,
                    data.switching, data.total,
                )
            else:
                logger.debug("    xreplay %-20s  no level-0 row found", stage_name)
        return result

    def _parse_xreplay_log(
        self, log_path: str, stage_name: str
    ) -> Optional[XReplayData]:
        """
        Find the level-0 row in an XReplay power log and extract metrics.

        Basic format  (8 fields):
            Cells  Pct%  Leakage  Internal  Switching  Total  Lvl  Instance

        Extended format  (11 fields, adds TG/IG breakdown):
            Cells  Pct%  Leakage  Internal  Switching
            TGInternal  TGSwitching  IGInternal  Total  Lvl  Instance

        Detection: second-to-last field == "0" and last field starts with "/"
        (i.e. grep "0 /")
        """
        data = XReplayData(stage_name=stage_name)
        found = False
        try:
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 8:
                        continue
                    # Level-0 row: Lvl="0", Instance starts with "/"
                    if parts[-2] != "0" or not parts[-1].startswith("/"):
                        continue
                    n_fields = len(parts)
                    if n_fields == 8:
                        # Basic: Cells Pct Leakage Internal Switching Total Lvl Instance
                        data.leakage   = parts[2]
                        data.internal  = parts[3]
                        data.switching = parts[4]
                        data.total     = parts[5]
                    elif n_fields == 9:
                        # Basic + Glitch: ... Switching Total Glitch Lvl Instance
                        data.leakage   = parts[2]
                        data.internal  = parts[3]
                        data.switching = parts[4]
                        data.total     = parts[5]
                        data.glitch    = parts[6]
                    elif n_fields == 11:
                        # Extended: ... Switching TGInternal TGSwitching IGInternal Total Lvl Instance
                        data.leakage      = parts[2]
                        data.internal     = parts[3]
                        data.switching    = parts[4]
                        data.tg_internal  = parts[5]
                        data.tg_switching = parts[6]
                        data.ig_internal  = parts[7]
                        data.total        = parts[8]
                    elif n_fields >= 12:
                        # Extended + Glitch: ... IGInternal Total Glitch Lvl Instance
                        data.leakage      = parts[2]
                        data.internal     = parts[3]
                        data.switching    = parts[4]
                        data.tg_internal  = parts[5]
                        data.tg_switching = parts[6]
                        data.ig_internal  = parts[7]
                        data.total        = parts[8]
                        data.glitch       = parts[9]
                    else:
                        # Fallback: read Total/Switching/Internal/Leakage from right
                        data.total     = parts[-3]
                        data.switching = parts[-4]
                        data.internal  = parts[-5]
                        data.leakage   = parts[-6]
                    found = True
                    break   # Only the first level-0 row is needed
        except Exception as exc:
            logger.error("Error parsing XReplay log %s: %s", log_path, exc)
            return None

        return data if found else None

    def _extract_syn_wirelength(self, work_dir: str) -> Tuple[str, str]:
        """
        Extract wirelength (um) and via count from syn/logs/syn.log.

        Pattern (last occurrence wins if the log contains multiple runs):
            [NR-eGR] Total length: 7667569um, number of vias: 13942034

        Returns:
            (wirelength_um, via_count) — both as strings, empty if not found.
        """
        syn_log = os.path.join(work_dir, "syn", "logs", "syn.log")
        wire_re = re.compile(
            r'\[NR-eGR\] Total length:\s*(\d+)um,\s*number of vias:\s*(\d+)'
        )
        wirelength = ""
        via_count = ""
        try:
            with open(syn_log, "r", errors="ignore") as f:
                for line in f:
                    m = wire_re.search(line)
                    if m:
                        wirelength = m.group(1)
                        via_count  = m.group(2)
                        # keep iterating — last match wins
        except Exception as exc:
            logger.debug("Could not extract wirelength from %s: %s", syn_log, exc)

        if wirelength:
            logger.debug("  wirelength=%sum  vias=%s", wirelength, via_count)
        else:
            logger.debug("  wirelength: not found in %s", syn_log)
        return wirelength, via_count

    def _extract_gate_statistics(self, work_dir: str) -> Optional[GateStatisticsData]:
        """
        Extract gate statistics from syn/reports/map/map_gates.rpt.

        Returns GateStatisticsData with counts of cell types, complexity, and drive strength.
        Returns None if file not found or parsing fails.
        """
        rpt_path = os.path.join(work_dir, "syn", "reports", "map", "map_gates.rpt")

        if not os.path.isfile(rpt_path):
            logger.debug("  Gate statistics: map_gates.rpt not found at %s", rpt_path)
            return None

        data = self._parse_map_gates_file(rpt_path)

        if data:
            logger.debug(
                "  Gate statistics: Cell Types=%d categories  "
                "Complexity=%d categories  Drive Strength=%d categories  Total=%s",
                len(data.cell_types), len(data.complexity),
                len(data.drive_strength), data.total_instances
            )
        else:
            logger.debug("  Gate statistics: parse failed for %s", rpt_path)

        return data

    def _parse_map_gates_file(self, rpt_path: str) -> Optional[GateStatisticsData]:
        """
        Parse map_gates.rpt and count instances by cell type, complexity, and drive strength.

        Format: Each data line has cell name (col 1) and instance count (col 2).
        Example:
            AIOI21_NOM_D0P8_H132DPNPN_L3_P44_UL    625

        Patterns are matched case-insensitively against each cell name.
        Instance counts are summed for each matching pattern.
        A single cell type may match multiple patterns (e.g., both AIOI and D0P8).
        All matches accumulate independently.

        Returns GateStatisticsData with summed instance counts, or None on parse failure.
        """
        data = GateStatisticsData()

        # Define canonical order for each category (matches requirements)
        CELL_TYPES = ["LLL", "L", "ULLL", "UL", "EL", "APK", "PNPN", "PNNP", "NPPN", "OHHN", "OHHP"]
        COMPLEXITY = ["OAOI", "OAI", "OIAI", "OA", "MOAI", "IAO", "HA", "FA", "AOI", "AO", "AIOI"]
        DRIVE_STR = ["D0P5", "D0P6", "D0P8", "D1", "D1P2", "D1P6", "D2", "D3", "D4", "D6", "D8",
                     "D10", "D12", "D14", "D16", "D18", "D20", "D24", "D32", "D48"]

        # Initialize counters as integers (not strings)
        counters = {
            'cell_types': {ct: 0 for ct in CELL_TYPES},
            'complexity': {cx: 0 for cx in COMPLEXITY},
            'drive_strength': {ds: 0 for ds in DRIVE_STR},
            'total': 0,
        }

        try:
            with open(rpt_path, "r", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()

                    # Skip empty or header lines (lines with separators or headers)
                    if not stripped or stripped.startswith("*") or stripped.startswith("-") or "Gate" in line or "Instances" in line or stripped.lower().startswith("total"):
                        continue

                    # Extract cell/instance name and instance count
                    parts = stripped.split()
                    if len(parts) < 2:
                        continue

                    cell_name = parts[0].upper()  # Case-insensitive matching

                    # Try to parse instance count from second column
                    try:
                        instance_count = int(float(parts[1]))
                    except (ValueError, IndexError):
                        # If we can't parse the second column as a number, skip this line
                        continue

                    # Add to total (sum of all instance counts)
                    counters['total'] += instance_count

                    # Check each pattern independently and add instance count to matching patterns
                    for cell_type in CELL_TYPES:
                        if cell_type.upper() in cell_name:
                            counters['cell_types'][cell_type] += instance_count

                    for cx in COMPLEXITY:
                        if cx.upper() in cell_name:
                            counters['complexity'][cx] += instance_count

                    for ds in DRIVE_STR:
                        if ds.upper() in cell_name:
                            counters['drive_strength'][ds] += instance_count

            # Convert counts to strings and store in data
            for ct in CELL_TYPES:
                if counters['cell_types'][ct] > 0:
                    data.cell_types[ct] = str(counters['cell_types'][ct])
                    data.cell_types_order.append(ct)

            for cx in COMPLEXITY:
                if counters['complexity'][cx] > 0:
                    data.complexity[cx] = str(counters['complexity'][cx])
                    data.complexity_order.append(cx)

            for ds in DRIVE_STR:
                if counters['drive_strength'][ds] > 0:
                    data.drive_strength[ds] = str(counters['drive_strength'][ds])
                    data.drive_strength_order.append(ds)

            data.total_instances = str(counters['total'])

            return data if counters['total'] > 0 else None

        except Exception as exc:
            logger.error("Error parsing map_gates.rpt %s: %s", rpt_path, exc)
            return None

    def _extract_pnr_timing(self, log_path: str, stage_name: str) -> Optional[PnrTimingData]:
        """
        Extract timing metrics from the last report_timing section in a PNR log.

        Methodology: Find the LAST "Setup mode" line, then extract timing data
        from the predictable lines that follow within that section.

        Extracts:
          - Corner name (inferred from first data row)
          - Density value
          - Path group WNS/TNS/FailingPaths from the table rows
          - HEPG weighted WNS/TNS
          - All Paths WNS/TNS/FailingPaths
        """
        data = PnrTimingData(stage_name=stage_name)

        try:
            with open(log_path, "r", errors="ignore") as f:
                all_lines = f.readlines()

            # Find the LAST "Setup mode" occurrence
            last_setup_idx = -1
            for i, line in enumerate(all_lines):
                if "Setup mode" in line:
                    last_setup_idx = i
                    logger.debug("    timing: Setup mode found at line %d: %s",
                                 i, line.rstrip()[:80])

            if last_setup_idx == -1:
                logger.debug("  PNR timing %-10s  no Setup mode found", stage_name)
                return None

            # Extract path group names directly from the Setup mode HEADER row
            # The row contains: |Setup mode|all|default|FromDCMacro|FromLsuAgu|...
            setup_mode_header_line = all_lines[last_setup_idx]
            header_names = [v.strip() for v in setup_mode_header_line.split("|")]
            header_names = [v for v in header_names if v and v != "Setup mode"]
            logger.debug("    timing: header path groups (%d): %s",
                         len(header_names), header_names[:8])

            # Build ID map from lines before Setup mode as fallback
            id_table_re = re.compile(r"^\s*\|\s*(\d+)\s*\|\s*([^\|]+?)\s*\|")
            id_map: Dict[str, str] = {}
            for i in range(last_setup_idx - 1, max(0, last_setup_idx - 50), -1):
                m = id_table_re.match(all_lines[i])
                if m:
                    id_num = m.group(1).strip()
                    pg_name = m.group(2).strip()
                    if id_num.isdigit() and pg_name:
                        id_map[id_num] = pg_name
                # Stop at separator line
                if all_lines[i].strip().startswith("+") and "---" in all_lines[i]:
                    break
            if id_map:
                logger.debug("    timing: ID map (%d entries, fallback): %s",
                             len(id_map), ", ".join(f"{k}={v}" for k, v in list(id_map.items())[:4]))

            # Expand condensed names like "1:Fro..ro" → full name using id_map
            if id_map:
                expanded_names = []
                for n in header_names:
                    m2 = re.match(r'^(\d+):', n)
                    if m2:
                        expanded_names.append(id_map.get(m2.group(1), n))
                    else:
                        expanded_names.append(n)
                header_names = expanded_names
                logger.debug("    timing: expanded header names: %s", header_names[:8])

            # Scan forward from Setup mode for timing data
            in_timing_section = True
            corner_line = ""
            density_line = ""
            wns_line = ""
            tns_line = ""
            paths_line = ""
            hepg_line = ""
            all_paths_line = ""
            mbff_line = ""
            drv_line = ""

            for i in range(last_setup_idx + 1, len(all_lines)):
                line = all_lines[i]
                stripped = line.strip()

                # Density line (before the table usually)
                if "Density:" in line:
                    density_line = line
                    m = re.search(r"Density:\s*([\d.]+%)", line)
                    if m:
                        data.density = m.group(1)

                # Corner (first pipe-delimited line with a dot)
                if not corner_line and "|" in line and "." in line and \
                   "Setup" not in line and "WNS" not in line and "TNS" not in line:
                    m = re.match(r"^\s*\|\s*([A-Za-z][A-Za-z0-9_.@\-]+)\s*\|", line)
                    if m:
                        candidate = m.group(1).strip()
                        if "." in candidate and candidate not in ("HEPG", "All"):
                            corner_line = line
                            data.corner_name = candidate
                            logger.debug("    timing: corner = %s", candidate)

                # WNS row
                if "WNS" in line and ("ns" in line or ":" in line) and "|" in line and not wns_line:
                    wns_line = line

                # TNS row
                if "TNS" in line and ("ns" in line or ":" in line) and "|" in line and not tns_line:
                    tns_line = line

                # Paths/Violating Paths row (can have various names: Failing, Violating, All Paths)
                if any(x in line for x in ("Violating", "Failing", "FEP", "All Paths", "Paths:")) and \
                   "|" in line and not paths_line and wns_line and tns_line:
                    paths_line = line

                # HEPG line
                if "HEPG" in line and "|" in line and not hepg_line:
                    m = re.search(r"\|\s*HEPG\s*\|\s*([-\d.]+)\s*\|\s*([-\d.e+]+)", line)
                    if m:
                        hepg_line = line
                        data.hepg_wns = m.group(1)
                        data.hepg_tns = m.group(2)

                # All Paths line
                if "All" in line and "Paths" in line and "|" in line and not all_paths_line:
                    m = re.search(r"\|\s*All\s+Paths\s*\|\s*([-\d.]+)\s*\|\s*([-\d.e+]+)", line)
                    if m:
                        all_paths_line = line
                        data.all_paths_wns = m.group(1)
                        data.all_paths_tns = m.group(2)
                        # Check for FP value after TNS
                        parts = [v.strip() for v in line.split("|")]
                        if len(parts) >= 4:
                            try:
                                data.all_paths_fp = parts[3]
                            except (IndexError, ValueError):
                                pass

                # MBFF line
                if "MBCI" in line and not mbff_line:
                    m = re.search(r"Total MBCI\s*:\s*([\d.]+(?:\([\d.]+\))?)", line)
                    if m:
                        mbff_line = line
                        data.mbff = m.group(1)

                # DRV line
                if "Cap" in line and "Violation" in line and "|" in line and not drv_line:
                    drv_line = line
                    data.drv = stripped

                # Stop at end markers
                if stripped.startswith("*****") and (wns_line or tns_line):
                    break
                if "Design Summary" in line and "****" in line:
                    break

            # Parse WNS/TNS/Paths from the extracted lines
            if wns_line and tns_line:
                wns_vals = [v.strip() for v in wns_line.split("|")]
                wns_vals = [v for v in wns_vals if v]
                # Skip the label (WNS (ns):)
                if wns_vals and ("WNS" in wns_vals[0]):
                    wns_vals = wns_vals[1:]

                tns_vals = [v.strip() for v in tns_line.split("|")]
                tns_vals = [v for v in tns_vals if v]
                # Skip the label
                if tns_vals and ("TNS" in tns_vals[0]):
                    tns_vals = tns_vals[1:]

                paths_vals = []
                if paths_line:
                    paths_vals = [v.strip() for v in paths_line.split("|")]
                    paths_vals = [v for v in paths_vals if v]
                    # Skip the label (can be Violating Paths, Failing Paths, All Paths, etc)
                    if paths_vals and any(x in paths_vals[0] for x in ("Violating", "Failing", "Paths", "Num")):
                        paths_vals = paths_vals[1:]

                # Build path_groups using HEADER NAMES from Setup mode row
                data.path_groups = {}
                for i, (wns_v, tns_v) in enumerate(zip(wns_vals, tns_vals)):
                    if wns_v or tns_v:
                        # Use header name if available, else fall back to ID map
                        if i < len(header_names):
                            pg_name = header_names[i]
                        else:
                            pg_num = str(i + 1)
                            pg_name = id_map.get(pg_num, f"PathGroup{pg_num}")

                        fp_v = paths_vals[i].strip() if i < len(paths_vals) else ""
                        data.path_groups[pg_name] = (wns_v, tns_v, fp_v)

                logger.debug(
                    "  PNR timing %-10s  corner=%-40s  density=%-8s  "
                    "path_groups=%d  HEPG=%s/%s  AllPaths=%s/%s",
                    stage_name, data.corner_name, data.density,
                    len(data.path_groups), data.hepg_wns, data.hepg_tns,
                    data.all_paths_wns, data.all_paths_tns,
                )
                for pg, (wns, tns, fp) in list(data.path_groups.items())[:5]:
                    logger.debug("    path_group %-30s  WNS=%s  TNS=%s  FP=%s", pg, wns, tns, fp)
                return data

        except Exception as exc:
            logger.error("Error parsing PNR timing log %s: %s", log_path, exc)
            return None

        logger.debug("  PNR timing %-10s  incomplete (no WNS/TNS found)", stage_name)
        return None

    def _extract_pnr_power_all(
        self, log_path: str, stage_name: str
    ) -> Dict[str, PnrPowerData]:
        """
        Extract ALL power reports from a PNR log, keyed by source type.

        A single log may contain multiple annotation/power sections:
          - innovus  (annotation < 100%)
          - xreplay  (annotation = 100% in opt log)
          - multi_vcd_xreplay  (annotation = 100% in export-default log)

        Returns a dict: { "innovus": PnrPowerData, "xreplay": PnrPowerData, ... }
        """
        reports: Dict[str, PnrPowerData] = {}
        self._scan_power_from_file(log_path, stage_name, reports)

        # Also look for export-default log files in the same directory
        log_dir = os.path.dirname(log_path)
        dir_name = os.path.basename(os.path.dirname(log_dir))  # e.g. "placeopt"
        for export_log in glob.glob(os.path.join(log_dir, f"{dir_name}*export*default*.log")):
            if export_log != log_path:
                real_export = os.path.realpath(export_log) if os.path.islink(export_log) else export_log
                if os.path.isfile(real_export):
                    logger.debug("  PNR power  %-10s  scanning export log: %s",
                                 stage_name, os.path.basename(real_export))
                    self._scan_power_from_file(real_export, stage_name, reports)

        for src, pdata in reports.items():
            logger.debug(
                "  PNR power  %-10s  source=%-18s  Int=%s  Sw=%s  Leak=%s  Total=%s  groups=%d",
                stage_name, src,
                pdata.total_internal, pdata.total_switching,
                pdata.total_leakage, pdata.total_power,
                len(pdata.groups),
            )
            for gname, gvals in pdata.groups.items():
                logger.debug(
                    "    group %-28s  Int=%-8s  Sw=%-8s  Leak=%-8s  Tot=%-8s  %%=%s",
                    gname,
                    gvals.get("internal", "-"), gvals.get("switching", "-"),
                    gvals.get("leakage", "-"),  gvals.get("total", "-"),
                    gvals.get("percentage", "-"),
                )
        if not reports:
            logger.debug("  PNR power  %-10s  no annotation summary found", stage_name)
        return reports

    def _scan_power_from_file(
        self,
        log_path: str,
        stage_name: str,
        reports: Dict[str, PnrPowerData],
    ) -> None:
        """
        Scan a single log file for power reports and append to reports dict.

        Each "Activity annotation summary:" starts a new power section.
        Multiple sections may exist in the same file.
        """
        annotation_re = re.compile(r"Activity annotation summary:")
        total_nets_re = re.compile(r"Total Nets\s*:\s*([\d.]+)/([\d.]+)\s*=\s*([\d.%]+)")
        # Match power value lines like "Total Internal Power : 100.079" (may have % or other columns after)
        power_val_re = re.compile(r"^\s*([\w\s]+?)\s*:\s+([\d.eE+\-]+)")

        # State for current power section
        power_source = ""
        annotated_pct = ""
        in_annotation = False
        in_power_totals = False
        in_group_section = False
        cur_internal = ""
        cur_switching = ""
        cur_leakage = ""
        cur_power = ""
        cur_groups: Dict[str, Dict[str, str]] = {}

        def _commit_power() -> None:
            """Finalize current power section and store in reports."""
            nonlocal power_source, annotated_pct, in_annotation
            nonlocal in_power_totals, in_group_section
            nonlocal cur_internal, cur_switching, cur_leakage, cur_power, cur_groups
            if power_source and (cur_internal or cur_power or cur_groups):
                pdata = PnrPowerData(
                    stage_name=stage_name,
                    power_source=power_source,
                    total_internal=cur_internal,
                    total_switching=cur_switching,
                    total_leakage=cur_leakage,
                    total_power=cur_power,
                    groups=cur_groups.copy(),
                )
                reports[power_source] = pdata
            # Reset for next section
            power_source = ""
            annotated_pct = ""
            in_annotation = False
            in_power_totals = False
            in_group_section = False
            cur_internal = ""
            cur_switching = ""
            cur_leakage = ""
            cur_power = ""
            cur_groups = {}

        try:
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()

                    # New annotation summary → commit previous and start new
                    if annotation_re.search(line):
                        _commit_power()
                        in_annotation = True
                        continue

                    if in_annotation:
                        m = total_nets_re.search(line)
                        if m:
                            annotated_pct = m.group(3)
                            pct_val = float(annotated_pct.rstrip("%"))
                            if pct_val < 100:
                                power_source = "innovus"
                            elif "export-default" in log_path or "export_default" in log_path:
                                power_source = "multi_vcd_xreplay"
                            else:
                                power_source = "xreplay"
                            in_annotation = False
                            in_power_totals = True
                            continue

                    # Extract total power values
                    if in_power_totals:
                        m = power_val_re.match(line)
                        if m:
                            label = m.group(1).strip()
                            value = m.group(2)
                            if "Internal" in label and "Input" not in label and \
                               "Net" not in label:
                                cur_internal = value
                            elif "Switching" in label and "Input" not in label and \
                                 "Net" not in label:
                                cur_switching = value
                            elif "Leakage" in label:
                                cur_leakage = value
                            elif "Total" in label and "Power" in label:
                                cur_power = value

                        # Detect per-group section header (has Percentage column)
                        if "Percentage" in line and ("Internal" in line or "%" in line):
                            in_power_totals = False
                            in_group_section = True
                            continue

                    # Extract per-group power breakdown
                    if in_group_section:
                        # Total row ends the group section
                        if stripped.startswith("Total") and "100" in stripped:
                            _commit_power()
                            continue
                        # Separator lines
                        if stripped.startswith("-") or stripped.startswith("=") or not stripped:
                            continue

                        # Parse: GroupName  Internal  Switching  Leakage  Total  Percentage
                        parts = stripped.split()
                        if len(parts) >= 5:
                            try:
                                pct = parts[-1]
                                if "." in pct or pct.endswith("%"):
                                    total_v = parts[-2]
                                    leakage_v = parts[-3]
                                    switching_v = parts[-4]
                                    internal_v = parts[-5]
                                    group_name = " ".join(parts[:-5]).strip()

                                    if group_name and group_name[0].isupper():
                                        cur_groups[group_name] = {
                                            "internal": internal_v,
                                            "switching": switching_v,
                                            "leakage": leakage_v,
                                            "total": total_v,
                                            "percentage": pct,
                                        }
                            except (ValueError, IndexError):
                                pass

            # Commit any remaining power section at end of file
            _commit_power()

        except Exception as exc:
            logger.error("Error parsing PNR power log %s: %s", log_path, exc)

    # ------------------------------------------------------------------
    # PNR extraction
    # ------------------------------------------------------------------
    def _extract_pnr_stages_stub(self, work_dir: str) -> List[PnrStageData]:
        """
        Extract PNR stage data (timing, power, and misc metrics) from per-stage logs.

        Only the three data-bearing stages are processed and returned:
            placeopt, clockopt, routeopt

        Per-stage logs live at:
            work_dir/pnr/<stage>/logs/<stage>.log
        These are typically softlinks to the actual log file; Python's open()
        follows symlinks automatically.  The resolved real path is logged at
        DEBUG level so problems are visible with --debug.
        """
        # Ordered: only the three stages that carry timing/power/misc data
        _PNR_DATA_STAGES = ("placeopt", "clockopt", "routeopt")

        pnr_stages: List[PnrStageData] = []
        pnr_root = os.path.join(work_dir, "pnr")
        if not os.path.isdir(pnr_root):
            logger.debug("  PNR root not found: %s", pnr_root)
            return pnr_stages

        logger.debug("  PNR root: %s", pnr_root)

        for dir_name in _PNR_DATA_STAGES:
            stage_name = dir_name.upper()
            log_path = os.path.join(pnr_root, dir_name, "logs", f"{dir_name}.log")

            # --- Symlink resolution + existence check ---
            if os.path.islink(log_path):
                real_path = os.path.realpath(log_path)
                logger.debug(
                    "  PNR %-12s symlink: %s -> %s",
                    dir_name, log_path, real_path,
                )
                if not os.path.isfile(real_path):
                    logger.debug(
                        "  PNR %-12s symlink target missing — skipping", dir_name
                    )
                    continue
                # Use the resolved path so reads always succeed
                log_path = real_path
            elif os.path.isfile(log_path):
                logger.debug("  PNR %-12s log found (plain file): %s", dir_name, log_path)
            else:
                logger.debug("  PNR %-12s log not found: %s — skipping", dir_name, log_path)
                continue

            logger.info("  PNR %-12s processing: %s", dir_name, log_path)

            pnr_data = PnrStageData(
                stage_name=stage_name,
                logfile_path=log_path,
                metrics={},
            )

            # Timing
            pnr_data.timing = self._extract_pnr_timing(log_path, stage_name)
            if pnr_data.timing:
                logger.debug(
                    "  PNR %-12s timing OK  corner=%-40s  path_groups=%d  "
                    "HEPG_WNS=%s  AllPaths_WNS=%s",
                    dir_name,
                    pnr_data.timing.corner_name,
                    len(pnr_data.timing.path_groups),
                    pnr_data.timing.hepg_wns,
                    pnr_data.timing.all_paths_wns,
                )
            else:
                logger.debug("  PNR %-12s timing NOT extracted", dir_name)

            # Power (may contain multiple sources: innovus, xreplay, multi_vcd)
            pnr_data.power_reports = self._extract_pnr_power_all(log_path, stage_name)
            if pnr_data.power_reports:
                for src, pwr in pnr_data.power_reports.items():
                    logger.debug(
                        "  PNR %-12s power  OK  source=%-18s  Total=%s  groups=%d",
                        dir_name, src, pwr.total_power, len(pwr.groups),
                    )
            else:
                logger.debug("  PNR %-12s power  NOT extracted", dir_name)

            # Misc metrics
            pnr_data.metrics = self._extract_pnr_metrics(log_path, pnr_root, dir_name)
            if pnr_data.metrics:
                logger.debug(
                    "  PNR %-12s misc metrics: %s",
                    dir_name,
                    "  ".join(f"{k}={v}" for k, v in pnr_data.metrics.items()),
                )
            else:
                logger.debug("  PNR %-12s misc metrics: none extracted", dir_name)

            pnr_stages.append(pnr_data)
            logger.info(
                "  PNR %-12s done  timing=%s  power=%s  misc_keys=%d",
                dir_name,
                "OK" if pnr_data.timing else "NONE",
                "OK" if pnr_data.power_reports else "NONE",
                len(pnr_data.metrics),
            )

        logger.info("  PNR total stages extracted: %d", len(pnr_stages))
        return pnr_stages

    def _extract_pnr_metrics(
        self, log_path: str, pnr_root: str, stage_dir: str
    ) -> Dict[str, str]:
        """
        Extract miscellaneous PNR metrics from per-stage log.

        Metrics extracted:
          - Density: from "Density:" pattern, stop at "("
          - Wirelength: from "NR-eGR.*Total length:" pattern, stop at "um"
          - Active Wirelength: from wirelength.rpt file
          - Hotspot: from "hotspot area" pattern, stop at " "
          - CRR Commits: from "commits:" pattern
          - Runtime: from "real" pattern, stop at ","
          - MBFF: from MBFF report file "MBCI" pattern
          - Innovus Version: from "Version" pattern, stop at ", built"
        """
        metrics: Dict[str, str] = {}

        # Density: from log, last occurrence
        density = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"Density:",
            delete_to_match="Density:",
            secondary_pattern=r"\(",
            occurrence=-1,
        )
        if density:
            metrics["Density"] = density

        # Wirelength: from log, last occurrence
        wirelength = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"\[NR-eGR\].*Total length:",
            delete_to_match="[NR-eGR] Total length: ",
            secondary_pattern=r"um",
            occurrence=-1,
        )
        if wirelength:
            metrics["Wirelength"] = wirelength

        # MBFF: from newest *MBFF* report file in reports/, fallback to log
        # Report file format: "MBF coverage ... MBCI(%) (bits/gate) : 86.319(3.715)"
        # Log file fallback format: "Total MBCI : 86.319(3.715)"
        stage_name = os.path.basename(os.path.dirname(log_path))
        reports_dir = os.path.join(pnr_root, stage_dir, "reports")
        mbff_files = sorted(
            glob.glob(os.path.join(reports_dir, "*MBFF*")),
            key=os.path.getmtime,
            reverse=True,
        )
        if mbff_files:
            mbff_val = self._extract_metric_by_pattern(
                mbff_files[0],
                initial_pattern=r"MBCI.*bits/gate",
                delete_to_match="MBCI(%) (bits/gate) : ",
                secondary_pattern=None,
                occurrence=-1,
            )
        else:
            # Fallback: search log file for "Total MBCI : value"
            mbff_val = self._extract_metric_by_pattern(
                log_path,
                initial_pattern=r"MBCI",
                delete_to_match=": ",
                secondary_pattern=None,
                occurrence=-1,
            )
        if mbff_val:
            metrics["MBFF"] = mbff_val.strip()

        # Active Wirelength: from wirelength.rpt or main log
        # Line format: "Total Active Wire Length = 6618570.931"
        # NOTE: use stage_dir (e.g. "placeopt"), NOT stage_name which resolves to "logs"
        wl_rpt_path = os.path.join(
            pnr_root, stage_dir, "reports", f"{stage_dir}.wirelength.rpt"
        )
        if os.path.isfile(wl_rpt_path):
            active_wl_src = wl_rpt_path
            logger.debug("  Active WL  %-10s  using report file: %s", stage_dir, wl_rpt_path)
        else:
            active_wl_src = log_path
            logger.debug(
                "  Active WL  %-10s  report file not found (%s), falling back to log: %s",
                stage_dir, wl_rpt_path, os.path.basename(log_path),
            )
        active_wl = self._extract_metric_by_pattern(
            active_wl_src,
            initial_pattern=r"Total Active Wire Length",
            delete_to_match="Total Active Wire Length = ",
            secondary_pattern=None,
            occurrence=-1,
        )
        if active_wl:
            logger.debug("  Active WL  %-10s  extracted: %r", stage_dir, active_wl)
            metrics["Active Wirelength"] = active_wl
        else:
            logger.debug(
                "  Active WL  %-10s  pattern 'Total Active Wire Length' NOT FOUND in %s",
                stage_dir, os.path.basename(active_wl_src),
            )

        # Hotspot: from log, last occurrence
        hotspot = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"hotspot area",
            delete_to_match="= ",
            secondary_pattern=r"\s+\(",
            occurrence=-1,
        )
        if hotspot:
            metrics["Hotspot(max/tot)"] = hotspot

        # CRR Commits: from log, last occurrence
        crr = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"commits:",
            delete_to_match="commits: ",
            secondary_pattern=None,
            occurrence=-1,
        )
        if crr:
            metrics["CRR Commits"] = crr

        # Runtime: from log, last occurrence
        # Line format: --- Ending "Innovus" (totcpu=389:50:10, real=34:39:23, mem=...) ---
        runtime = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"real\s*=",
            delete_to_match="real=",
            secondary_pattern=r",",
            occurrence=-1,
        )
        if runtime:
            metrics["Runtime"] = runtime

        # Innovus Version: from log, first occurrence
        innovus_version = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"Version\s+v",
            delete_to_match="Version v",
            secondary_pattern=r",",
            occurrence=1,
        )
        if innovus_version:
            metrics["Innovus Version"] = innovus_version

        # DRC: from DRC report file if it exists
        stage_name_lower = stage_dir
        drc_files = glob.glob(
            os.path.join(pnr_root, stage_name_lower, "reports", f"{stage_name_lower}*.drc.rpt")
        )
        if not drc_files:
            drc_files = glob.glob(
                os.path.join(pnr_root, stage_name_lower, "reports", "*DRC*")
            )
        if drc_files:
            drc_path = sorted(drc_files, key=os.path.getmtime)[-1]
            drc_val = self._extract_metric_by_pattern(
                drc_path,
                initial_pattern=r"Total\s+(Violations|violations|DRC)",
                delete_to_match=":",
                secondary_pattern=None,
                occurrence=-1,
            )
            if drc_val:
                metrics["DRC"] = drc_val

        # Machine info: from log, detect CPU/core configuration
        machine = self._extract_metric_by_pattern(
            log_path,
            initial_pattern=r"Running on.*with",
            delete_to_match="Running on ",
            secondary_pattern=None,
            occurrence=1,
        )
        if machine:
            metrics["Machine"] = machine

        # Logfile: store the resolved log path for output
        metrics["Logfile"] = log_path

        if metrics:
            logger.debug("  PNR misc metrics for %-10s: %s", stage_name,
                         "  ".join(f"{k}={v}" for k, v in metrics.items()))
        else:
            logger.debug("  PNR misc metrics for %-10s: none extracted", stage_name)

        return metrics

    def _extract_metric_by_pattern(
        self,
        file_path: str,
        initial_pattern: str,
        delete_to_match: str,
        secondary_pattern: Optional[str],
        occurrence: int,
    ) -> str:
        """
        Extract a metric from a file using regex patterns.

        Logic mirrors the tcsh get_custom_metric.tcsh script:
        1. grep file_path for initial_pattern
        2. Take the Nth occurrence (positive) or Nth from end (negative)
        3. Remove everything up to and including delete_to_match
        4. If secondary_pattern provided, remove everything from that pattern onwards
        5. Return the result, stripped of whitespace
        """
        if not os.path.isfile(file_path):
            return ""

        try:
            with open(file_path, "r", errors="ignore") as f:
                lines = f.readlines()

            # Find all lines matching initial_pattern
            pattern = re.compile(initial_pattern)
            matching_lines = [l for l in lines if pattern.search(l)]

            if not matching_lines:
                return ""

            logger.debug(
                "    pattern=%r  file=%s  matches=%d  occurrence=%d",
                initial_pattern, os.path.basename(file_path),
                len(matching_lines), occurrence,
            )

            # Select by occurrence (positive = Nth, negative = from end)
            if occurrence > 0:
                if occurrence > len(matching_lines):
                    logger.debug("    -> no match (occurrence > count)")
                    return ""
                selected_line = matching_lines[occurrence - 1]
            else:  # occurrence < 0
                if abs(occurrence) > len(matching_lines):
                    logger.debug("    -> no match (|occurrence| > count)")
                    return ""
                selected_line = matching_lines[occurrence]

            logger.debug("    selected line: %s", selected_line.rstrip())

            # Remove everything up to and including delete_to_match
            # (delete_to_match is treated as a literal string, not a regex)
            delete_pattern = re.compile(f"^.*?{re.escape(delete_to_match)}")
            result = delete_pattern.sub("", selected_line)

            # Remove everything FROM secondary_pattern onwards (inclusive).
            # Use split so the pattern AND everything after it is discarded,
            # keeping only what precedes the first match.
            if secondary_pattern:
                secondary_re = re.compile(secondary_pattern)
                parts = secondary_re.split(result, maxsplit=1)
                result = parts[0]

            value = result.strip()
            logger.debug("    -> value: %r", value)
            return value

        except Exception as exc:
            logger.debug("Error extracting metric from %s: %s", file_path, exc)
            return ""

    def _extract_innovus_version_stub(self, work_dir: str) -> str:
        """STUB: Extract Innovus version string from PNR log."""
        return ""

    def _extract_os_stub(self, work_dir: str) -> str:
        """STUB: Extract OS string from log (e.g. 'Red Hat Enterprise Linux 8.10')."""
        return ""

    def _extract_machine_stub(self, work_dir: str) -> str:
        """STUB: Extract machine descriptor (e.g. '32cores_64cpus_AMD')."""
        return ""

    def _extract_ple_stub(self, work_dir: str) -> str:
        """STUB: Extract PLE_USED values from SYN log."""
        return ""

    def _extract_wcmfsp_stub(self, work_dir: str) -> str:
        """STUB: Extract WCMFSP value."""
        return ""

    # ------------------------------------------------------------------
    # Table assembly
    # ------------------------------------------------------------------
    def _build_rows(
        self, trials: List[TrialData], baseline_idx: Optional[int] = None
    ) -> List[List[str]]:
        """
        Assemble the full row-major table.

        Row schema:
          [row_label, trial_0_value, trial_1_value, ...]

        Layout:
          Header rows
          [blank]
          SYN stage sections (WNS/TNS combined, durations collected)
          Cell Depth sections (ordered by actual CSV stage order)
          XReplay Power sections (ordered by actual CSV stage order)
          PNR stage sections (timing, power, misc — runtime collected)
          Durations section (all SYN + PNR runtimes side-by-side)
          [blank]
          Footer (PATH)
        """
        rows: List[List[str]] = []
        n = len(trials)
        blank = [""] * (n + 1)  # Empty separator row

        def _row(label: str, vals: List[str]) -> List[str]:
            return [label] + list(vals)

        def _get(t: TrialData, attr: str, fallback: str = NA) -> str:
            v = getattr(t, attr, "") or fallback
            return v if v else fallback

        # --- Header ---
        rows.append(_row("Title",          [_get(t, "trial_name")      for t in trials]))
        rows.append(_row("Block Name",     [_get(t, "block_name")      for t in trials]))
        rows.append(_row("Innovus Version",[_get(t, "innovus_version") for t in trials]))
        rows.append(_row("Version",        [_get(t, "version")         for t in trials]))
        rows.append(blank)

        # --- Build preferred SYN stage order from actual CSV data ---
        preferred_syn_order: List[str] = []
        _seen_order: Dict[str, bool] = {}
        for t in trials:
            for s in t.syn_stages:
                if s.stage_name not in _seen_order:
                    preferred_syn_order.append(s.stage_name)
                    _seen_order[s.stage_name] = True

        # Accumulate durations to emit at the end
        # Ordered list of (label, values_list) pairs
        duration_rows: List[Tuple[str, List[str]]] = []

        # --- Identical-trial detection ---
        # For each trial, find the earliest other trial whose SYN data is
        # bit-for-bit identical.  The NEWER duplicate gets an "Identical To"
        # annotation; the older original is unmarked.
        fp_map = {t.work_dir: _syn_fingerprint(t) for t in trials}
        age_map = {t.work_dir: _trial_age(t) for t in trials}
        identical_to: Dict[str, str] = {}
        for t in trials:
            fp = fp_map[t.work_dir]
            if not fp:
                continue
            # Collect all OTHER trials with the same fingerprint, sorted oldest first
            peers = sorted(
                [t2 for t2 in trials
                 if t2.work_dir != t.work_dir and fp_map[t2.work_dir] == fp],
                key=lambda t2: age_map[t2.work_dir],
            )
            if peers:
                oldest_peer = peers[0]
                # Only flag this trial if an older identical trial exists
                if age_map[oldest_peer.work_dir] < age_map[t.work_dir]:
                    identical_to[t.work_dir] = oldest_peer.trial_name

        idt_vals = [identical_to.get(t.work_dir, "") for t in trials]
        if any(idt_vals):
            rows.append(_row("SYN Identical To", idt_vals))

        # --- SYN stage sections ---
        all_syn_stage_names = _union_stage_names(
            [t.syn_stages for t in trials], key=lambda s: s.stage_name
        )

        for stage_name in all_syn_stage_names:
            stage_vals = [_get_syn_stage(t, stage_name) for t in trials]
            rows.append(_row("Metric", [stage_name] * n))

            # WNS/TNS combined pairs — single "wns / tns" cell per row
            for wns_col, tns_col, label in _SYN_WNS_TNS_PAIRS:
                values = [
                    _combine_wns_tns(
                        s.metrics.get(wns_col, "no_value"),
                        s.metrics.get(tns_col, "no_value"),
                    ) if s else NA
                    for s in stage_vals
                ]
                if _should_show(values):
                    rows.append(_row(label, values))

            # Standalone metrics (no pairing)
            for col_idx, label in _SYN_SOLO_COLS:
                values = [
                    _coerce(s.metrics.get(col_idx, "no_value") if s else "no_value")
                    for s in stage_vals
                ]
                if _should_show(values):
                    rows.append(_row(label, values))

            # Wirelength and via count — emit only in the syn_gen section
            if stage_name == "syn_gen":
                wl_vals = [t.wirelength or NA for t in trials]
                vc_vals = [t.via_count  or NA for t in trials]
                if _should_show(wl_vals):
                    rows.append(_row("Wirelength(um)", wl_vals))
                if _should_show(vc_vals):
                    rows.append(_row("Via Count", vc_vals))

            # Runtime columns: show BOTH inline in each stage section.
            # Also add only RealRuntime (col 30) to the Durations section —
            # RealElapsed is shown here per-stage but not in the summary.
            for dur_col, dur_label in _SYN_DURATION_COLS:
                values = [
                    _coerce(s.metrics.get(dur_col, "no_value") if s else "no_value")
                    for s in stage_vals
                ]
                if _should_show(values):
                    rows.append(_row(dur_label, values))
                    # RealRuntime (first entry, col 30) also goes to Durations section
                    if dur_col == _SYN_DURATION_COLS[0][0]:
                        duration_rows.append((f"{stage_name} RealRuntime", values))

            rows.append(blank)

        # --- Cell Depth sections (ordered by file mtime from first trial with data) ---
        preferred_depth_order = next(
            (t.cell_depths_order for t in trials if t.cell_depths_order),
            preferred_syn_order,
        )
        all_depth_stage_names = _union_syn_stage_names(
            [t.cell_depths for t in trials],
            preferred_order=preferred_depth_order,
        )
        for stage_name in all_depth_stage_names:
            depth_vals = [t.cell_depths.get(stage_name) for t in trials]
            rows.append(_row("Cell Depth", [stage_name] * n))

            md_vals = [d.max_depth if d else NA for d in depth_vals]
            if _should_show(md_vals):
                rows.append(_row("Max Depth", [v or NA for v in md_vals]))

            # Bucket rows — union sorted numerically; missing → "0 0"
            all_buckets = _union_bucket_labels(
                [d.bucket_order if d else [] for d in depth_vals]
            )
            for bucket_label in all_buckets:
                values = []
                for d in depth_vals:
                    if d and bucket_label in d.buckets:
                        all_cnt, tot_cnt = d.buckets[bucket_label]
                        values.append(f"{all_cnt} {tot_cnt}")
                    else:
                        values.append("")  # blank — trial has no data for this bucket
                rows.append(_row(bucket_label, values))

            for attr, label in [
                ("total_cells",      "Total Cells"),
                ("total_cells_gt25", "Total Cells > 25"),
                ("pct_gt25",         "PCT of Cells > 25"),
                ("mean",             "Mean"),
                ("std_dev",          "Std Dev"),
            ]:
                vals = [
                    getattr(d, attr, "") or NA if d else NA
                    for d in depth_vals
                ]
                if _should_show(vals):
                    rows.append(_row(label, vals))

            rows.append(blank)

        # --- XReplay Power sections (ordered by file mtime from first trial with data) ---
        preferred_xreplay_order = next(
            (t.xreplay_order for t in trials if t.xreplay_order),
            preferred_syn_order,
        )
        all_xreplay_stage_names = _union_syn_stage_names(
            [t.xreplay_power for t in trials],
            preferred_order=preferred_xreplay_order,
        )
        for stage_name in all_xreplay_stage_names:
            xr_vals = [t.xreplay_power.get(stage_name) for t in trials]
            rows.append(_row("XReplay Power", [stage_name] * n))

            for attr, label in [
                ("leakage",      "Leakage(mW)"),
                ("internal",     "Internal(mW)"),
                ("switching",    "Switching(mW)"),
                ("glitch",       "Glitch(mW)"),
                ("total",        "Total(mW)"),
                ("tg_internal",  "TGInternal(mW)"),
                ("tg_switching", "TGSwitching(mW)"),
                ("ig_internal",  "IGInternal(mW)"),
            ]:
                vals = [
                    getattr(d, attr, "") or NA if d else NA
                    for d in xr_vals
                ]
                if _should_show(vals):
                    rows.append(_row(label, vals))

            rows.append(blank)

        # --- PNR stage sections ---
        all_pnr_stage_names = _union_stage_names(
            [t.pnr_stages for t in trials], key=lambda s: s.stage_name
        )

        # Power source display config: (source_key, total_prefix, group_prefix)
        _POWER_SOURCES = [
            ("innovus",            "Inn",   ""),
            ("xreplay",            "X",     "X "),
            ("multi_vcd_xreplay",  "Multi", "M "),
        ]
        # Power group display names: map internal names to short labels
        _GROUP_LABELS = {
            "Combinational":         "Combinational",
            "Sequential":            "Sequential",
            "Clock (Combinational)": "Clock Comb.",
            "Clock (Sequential)":    "Clock Seq.",
            "Macro":                 "Macro",
            "IO":                    "IO",
            "Input Net":             "Input Net",
        }

        for stage_name in all_pnr_stage_names:
            pnr_vals = [_get_pnr_stage(t, stage_name) for t in trials]
            rows.append(_row("Stage", [stage_name] * n))

            # --- PNR Timing ---
            timing_vals = [s.timing if s else None for s in pnr_vals]
            if any(tv is not None for tv in timing_vals):

                # Build reverse ID map for condensed names (from first trial with data)
                reverse_id_map: Dict[str, str] = {}
                for tv in timing_vals:
                    if tv and tv.path_groups:
                        # The path_groups were built from id_map: pg_name -> (wns,tns,fp)
                        # We need id -> name to condense. Re-derive from order.
                        for i, pg_name in enumerate(tv.path_groups.keys()):
                            reverse_id_map[pg_name] = str(i + 1)
                        break

                # --- All Paths first (overall worst slack) ---
                # Use path_groups["all"] which has correct WNS/TNS/FP from the timing table
                all_combined = []
                for tv in timing_vals:
                    if tv and "all" in tv.path_groups:
                        wns, tns, fp = tv.path_groups["all"]
                        all_combined.append(_combine_wns_tns(wns, tns, fp))
                    else:
                        all_combined.append(NA)
                if _should_show(all_combined):
                    rows.append(_row("all", all_combined))

                # --- default path group ---
                default_combined = []
                for tv in timing_vals:
                    if tv and "default" in tv.path_groups:
                        wns, tns, fp = tv.path_groups["default"]
                        default_combined.append(_combine_wns_tns(wns, tns, fp))
                    elif tv:
                        default_combined.append("0.000 / 0.000 / 0")
                    else:
                        default_combined.append(NA)
                if _should_show(default_combined):
                    rows.append(_row("default", default_combined))

                # --- Individual path groups (excluding default, order preserved) ---
                all_path_groups_ordered: List[str] = []
                _seen_pg: Dict[str, bool] = {}
                for tv in timing_vals:
                    if tv:
                        for pg_name in tv.path_groups.keys():
                            if pg_name not in ("default", "all") and pg_name not in _seen_pg:
                                all_path_groups_ordered.append(pg_name)
                                _seen_pg[pg_name] = True

                for pg_name in all_path_groups_ordered:
                    combined = []
                    for tv in timing_vals:
                        if tv and pg_name in tv.path_groups:
                            wns, tns, fp = tv.path_groups[pg_name]
                            combined.append(_combine_wns_tns(wns, tns, fp))
                        else:
                            combined.append(NA)
                    if _should_show(combined):
                        # Condense long path group names
                        pg_id = reverse_id_map.get(pg_name, "")
                        display_name = _condense_pg_name(pg_name, pg_id)
                        rows.append(_row(display_name, combined))

                # --- HEPG (pipe-delimited format) ---
                hepg_combined = [
                    f"|{tv.hepg_wns}|{tv.hepg_tns}|"
                    if tv and tv.hepg_wns else NA
                    for tv in timing_vals
                ]
                if _should_show(hepg_combined):
                    rows.append(_row("HEPG", hepg_combined))

                # --- Density ---
                density_vals = [tv.density if tv else NA for tv in timing_vals]
                if _should_show(density_vals):
                    rows.append(_row("Density", density_vals))

            # --- Misc metrics (Wirelength, Hotspot, DRV/DRC, etc.) ---
            # These come from the metrics dict, shown inline
            _INLINE_METRICS = [
                "MBFF", "Wirelength", "Active Wirelength", "Hotspot(max/tot)",
            ]
            for key in _INLINE_METRICS:
                values = [
                    _coerce(s.metrics.get(key, "no_value") if s else "no_value")
                    for s in pnr_vals
                ]
                if _should_show(values):
                    rows.append(_row(key, values))

            # --- PNR Power (per source: innovus, xreplay, multi_vcd) ---
            for src_key, total_prefix, grp_prefix in _POWER_SOURCES:
                # Get power data for this source from each trial
                src_power = [
                    s.power_reports.get(src_key) if s else None
                    for s in pnr_vals
                ]
                if not any(p is not None for p in src_power):
                    continue

                # Total power values
                total_vals = [p.total_power if p else NA for p in src_power]
                leak_vals  = [p.total_leakage if p else NA for p in src_power]
                sw_vals    = [p.total_switching if p else NA for p in src_power]
                int_vals   = [p.total_internal if p else NA for p in src_power]

                # DYNAMIC = switching + internal (summed numerically where available)
                def _sum_power(sw: str, int_: str) -> str:
                    try:
                        return str(float(sw) + float(int_))
                    except (ValueError, TypeError):
                        if sw and sw != NA:
                            return sw
                        return int_ if int_ else NA

                dyn_vals = [
                    _sum_power(sw, iv)
                    for sw, iv in zip(sw_vals, int_vals)
                ]

                # Replace empty with -not available-
                def _na_if_empty(v: str) -> str:
                    return v if v else "-not available-"

                total_out = [_na_if_empty(v) for v in total_vals]
                leak_out  = [_na_if_empty(v) for v in leak_vals]
                dyn_out   = [_na_if_empty(v) for v in dyn_vals]
                sw_out    = [_na_if_empty(v) for v in sw_vals]
                int_out   = [_na_if_empty(v) for v in int_vals]

                if _should_show(total_out):
                    rows.append(_row(f"{total_prefix} Power", total_out))
                if _should_show(leak_out):
                    rows.append(_row(f"{total_prefix} Leakage", leak_out))
                if _should_show(dyn_out):
                    rows.append(_row(f"{total_prefix} Dynamic", dyn_out))
                if _should_show(int_out):
                    rows.append(_row(f"{total_prefix} Internal", int_out))
                if _should_show(sw_out):
                    rows.append(_row(f"{total_prefix} Switching", sw_out))

                # Per-group totals — only the specified categories in order
                _SHOW_GROUPS = [
                    "Combinational",
                    "Sequential",
                    "Clock (Combinational)",
                    "Clock (Sequential)",
                ]
                for group_name in _SHOW_GROUPS:
                    group_total_vals = []
                    for p in src_power:
                        if p and group_name in p.groups:
                            g = p.groups[group_name]
                            group_total_vals.append(g.get("total", NA))
                        else:
                            group_total_vals.append(NA)
                    display_name = _GROUP_LABELS.get(group_name, group_name)
                    if _should_show(group_total_vals):
                        rows.append(_row(f"{grp_prefix}{display_name}", group_total_vals))

            # CRR Commits
            crr_vals = [
                _coerce(s.metrics.get("CRR Commits", "no_value") if s else "no_value")
                for s in pnr_vals
            ]
            if _should_show(crr_vals):
                rows.append(_row("CRR Commits", crr_vals))

            # Runtime → shown inline AND collected for Durations section
            runtime_vals = [
                _coerce(s.metrics.get("Runtime", "no_value") if s else "no_value")
                for s in pnr_vals
            ]
            if _should_show(runtime_vals):
                rows.append(_row("Runtime", runtime_vals))
                duration_rows.append((f"{stage_name} Runtime", runtime_vals))

            # Machine
            machine_vals = [
                _coerce(s.metrics.get("Machine", "no_value") if s else "no_value")
                for s in pnr_vals
            ]
            if _should_show(machine_vals):
                rows.append(_row("Machine", machine_vals))

            # Logfile
            logfiles = [s.logfile_path if s else NA for s in pnr_vals]
            rows.append(_row("Logfile", [lf or NA for lf in logfiles]))

            # Any remaining misc metrics not already output
            _HANDLED_KEYS = {
                "MBFF", "Density", "Wirelength", "Active Wirelength", "Hotspot(max/tot)",
                "DRV(cap/tran)", "DRC", "CRR Commits", "Runtime", "Machine",
                "Logfile", "Innovus Version",
            }
            all_keys = _union_metric_keys([s.metrics if s else {} for s in pnr_vals])
            for key in all_keys:
                if key in _HANDLED_KEYS:
                    continue
                values = [
                    _coerce(s.metrics.get(key, "no_value") if s else "no_value")
                    for s in pnr_vals
                ]
                if _should_show(values):
                    rows.append(_row(key, values))

            rows.append(blank)

        # --- Durations section (all stage runtimes side-by-side) ---
        if duration_rows:
            rows.append(_row("Durations", [""] * n))
            for dur_label, dur_vals in duration_rows:
                rows.append(_row(dur_label, dur_vals))
            rows.append(blank)

        # --- Gate Statistics section ---
        gate_stats_vals = [t.gate_statistics for t in trials]

        if any(gs is not None for gs in gate_stats_vals):
            rows.append(_row("Gate Statistics", [""] * n))

            # Cell Types subsection
            rows.append(_row("Cell Types", [""] * n))
            CANONICAL_CELL_TYPES = ["LLL", "L", "ULLL", "UL", "EL", "APK", "PNPN", "PNNP", "NPPN", "OHHN", "OHHP"]
            for cell_type in CANONICAL_CELL_TYPES:
                vals = [
                    gs.cell_types.get(cell_type, NA) if gs else NA
                    for gs in gate_stats_vals
                ]
                if _should_show(vals):
                    rows.append(_row(f"  {cell_type}", vals))
            rows.append(blank)

            # Cell Complexity subsection
            rows.append(_row("Cell Complexity", [""] * n))
            CANONICAL_COMPLEXITY = ["OAOI", "OAI", "OIAI", "OA", "MOAI", "IAO", "HA", "FA", "AOI", "AO", "AIOI"]
            for cx in CANONICAL_COMPLEXITY:
                vals = [
                    gs.complexity.get(cx, NA) if gs else NA
                    for gs in gate_stats_vals
                ]
                if _should_show(vals):
                    rows.append(_row(f"  {cx}", vals))
            rows.append(blank)

            # Drive Strength subsection
            rows.append(_row("Drive Strength", [""] * n))
            CANONICAL_DRIVE_STR = ["D0P5", "D0P6", "D0P8", "D1", "D1P2", "D1P6", "D2", "D3", "D4", "D6", "D8",
                                    "D10", "D12", "D14", "D16", "D18", "D20", "D24", "D32", "D48"]
            for ds in CANONICAL_DRIVE_STR:
                vals = [
                    gs.drive_strength.get(ds, NA) if gs else NA
                    for gs in gate_stats_vals
                ]
                if _should_show(vals):
                    rows.append(_row(f"  {ds}", vals))
            rows.append(blank)

            # Total Instances row
            total_vals = [gs.total_instances if gs else NA for gs in gate_stats_vals]
            if _should_show(total_vals):
                rows.append(_row("Total Instances", total_vals))

            rows.append(blank)

        # --- Footer ---
        rows.append(_row("PATH", [t.syn_log_path or NA for t in trials]))

        # ===== Baseline Comparison Sections =====
        if baseline_idx is not None and baseline_idx < len(trials):
            baseline = trials[baseline_idx]

            # --- Delta formatters ---
            def _try_float(s: str) -> Optional[float]:
                try:
                    return float(str(s or "").strip().rstrip("%"))
                except (ValueError, TypeError):
                    return None

            def _timing_delta(trial_v: str, base_v: str) -> str:
                """Absolute ns change vs baseline. + = degradation, - = improvement."""
                t = _try_float(trial_v)
                b = _try_float(base_v)
                if t is None or b is None:
                    return NA
                delta = t - b  # negative delta → trial more negative → degradation
                if abs(b) < 1e-9:
                    if abs(delta) < 1e-9:
                        return "ISO"
                    return f"+{abs(delta):.1f}" if delta < 0 else f"-{abs(delta):.1f}"
                pct = abs(delta) / abs(b) * 100
                if delta < 0:   # trial more negative = degradation
                    return "ISO" if pct < 0.5 else f"+{abs(delta):.1f}"
                else:           # trial less negative = improvement
                    return "ISO" if pct <= 0.4 else f"-{delta:.1f}"

            def _power_delta(trial_v: str, base_v: str) -> str:
                """% power change vs baseline. + = degradation, - = improvement."""
                t = _try_float(trial_v)
                b = _try_float(base_v)
                if t is None or b is None:
                    return NA
                if abs(b) < 1e-9:
                    return "ISO"
                delta = t - b
                pct = abs(delta) / abs(b) * 100
                if delta > 0:   # more power = degradation
                    return "ISO" if pct < 0.5 else f"+{pct:.2f}%"
                elif delta < 0: # less power = improvement
                    return "ISO" if pct <= 0.4 else f"-{pct:.2f}%"
                return "ISO"

            def _density_delta(trial_v: str, base_v: str) -> str:
                """Absolute % point change. Already in %, so threshold is on raw delta."""
                t = _try_float(trial_v)
                b = _try_float(base_v)
                if t is None or b is None:
                    return NA
                delta = t - b  # positive = higher density = degradation
                if delta > 0:
                    return "ISO" if delta < 0.5 else f"+{delta:.2f}"
                elif delta < 0:
                    return "ISO" if abs(delta) <= 0.4 else f"-{abs(delta):.2f}"
                return "ISO"

            def _get_pnr_power_val(
                pnr_s: Optional[PnrStageData], src_key: str, field: str
            ) -> str:
                if pnr_s is None:
                    return NA
                pd = pnr_s.power_reports.get(src_key)
                if pd is None:
                    return NA
                if field == "dynamic":
                    sw = _try_float(pd.total_switching)
                    iv = _try_float(pd.total_internal)
                    if sw is None and iv is None:
                        return NA
                    return str((sw or 0.0) + (iv or 0.0))
                return getattr(pd, field, NA) or NA

            def _get_tns_all(pnr_s: Optional[PnrStageData]) -> str:
                if pnr_s and pnr_s.timing and "all" in pnr_s.timing.path_groups:
                    return pnr_s.timing.path_groups["all"][1]
                return NA

            def _get_hepg_tns(pnr_s: Optional[PnrStageData]) -> str:
                if pnr_s and pnr_s.timing:
                    return pnr_s.timing.hepg_tns
                return NA

            def _get_density_str(pnr_s: Optional[PnrStageData]) -> str:
                if pnr_s and pnr_s.timing:
                    return pnr_s.timing.density
                return NA

            def _make_cmp_vals(
                stage_name: str,
                base_val: str,
                trial_val_fn,
                delta_fn,
            ) -> List[str]:
                """Build one row of comparison values (REF for baseline, delta for others)."""
                vals: List[str] = []
                for ci, ctrial in enumerate(trials):
                    if ci == baseline_idx:
                        vals.append("REF")
                        continue
                    tpnr = _get_pnr_stage(ctrial, stage_name)
                    tv = trial_val_fn(tpnr)
                    vals.append(delta_fn(tv, base_val))
                return vals

            def _has_cmp_data(vals: List[str]) -> bool:
                """True if any non-REF column has a real (non-NA) value."""
                return any(v and v not in (NA, "REF") for v in vals)

            rows.append(blank)
            rows.append(_row("Baseline Comparison", [""] * n))

            _CMP_STAGES = ("PLACEOPT", "CLOCKOPT", "ROUTEOPT")

            for cmp_stage in _CMP_STAGES:
                base_pnr = _get_pnr_stage(baseline, cmp_stage)

                rows.append(_row("Stage", [cmp_stage] * n))

                # ALL TNS
                base_tns = _get_tns_all(base_pnr)
                cmp_vals = _make_cmp_vals(cmp_stage, base_tns, _get_tns_all, _timing_delta)
                if _has_cmp_data(cmp_vals):
                    rows.append(_row("ALL TNS:", cmp_vals))

                # HEPG TNS
                base_hepg = _get_hepg_tns(base_pnr)
                cmp_vals = _make_cmp_vals(cmp_stage, base_hepg, _get_hepg_tns, _timing_delta)
                if _has_cmp_data(cmp_vals):
                    rows.append(_row("HEPG:", cmp_vals))

                # DENSITY
                base_dens = _get_density_str(base_pnr)
                cmp_vals = _make_cmp_vals(
                    cmp_stage, base_dens, _get_density_str, _density_delta
                )
                if _has_cmp_data(cmp_vals):
                    rows.append(_row("DENSITY:", cmp_vals))

                # Power comparisons
                _PWR_CMP = [
                    ("INN LEAKAGE:", "innovus",           "total_leakage"),
                    ("INN DYNAMIC:", "innovus",           "dynamic"),
                    ("X LEAKAGE:",   "xreplay",           "total_leakage"),
                    ("X DYNAMIC:",   "xreplay",           "dynamic"),
                    ("M LEAKAGE:",   "multi_vcd_xreplay", "total_leakage"),
                    ("M DYNAMIC:",   "multi_vcd_xreplay", "dynamic"),
                ]
                for pwr_label, src_key, field in _PWR_CMP:
                    base_v = _get_pnr_power_val(base_pnr, src_key, field)
                    trial_fn = (
                        lambda pnr_s, sk=src_key, f=field:
                        _get_pnr_power_val(pnr_s, sk, f)
                    )
                    cmp_vals = _make_cmp_vals(cmp_stage, base_v, trial_fn, _power_delta)
                    if _has_cmp_data(cmp_vals):
                        rows.append(_row(pwr_label, cmp_vals))

                rows.append(blank)

        return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_path_info(work_dir: str) -> Tuple[str, str, str]:
    """
    Extract (trial_name, block_name, stage) from a work_location path.

    Path structure: .../<trial_name>/<SYN|PNR>/<block_name>/iflowblocks/.../<trial_name>
    """
    parts = Path(work_dir).parts
    trial_name = parts[-1]  # Last component is always trial_name (second occurrence)
    stage = ""
    block_name = ""
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] in ("SYN", "PNR"):
            stage = parts[i]
            block_name = parts[i + 1] if i + 1 < len(parts) else ""
            break
    return trial_name, block_name, stage


def _find_syn_csv(work_dir: str) -> Optional[str]:
    """
    Find the most recently modified final.csv under work_dir.

    Tries <work_dir>/syn/reports/**/final.csv first (the canonical layout
    when work_dir is already the SYN work-location). Falls back to
    <work_dir>/**/syn/reports/**/final.csv so trial roots whose SYN data
    is nested under PNR/<block>/iflowblocks/<block>/imp/<run>/syn/reports/
    still resolve. Returns None if nothing matches.
    """
    shallow = os.path.join(work_dir, "syn", "reports", "**", "final.csv")
    matches = glob.glob(shallow, recursive=True)
    if not matches:
        deep = os.path.join(work_dir, "**", "syn", "reports", "**", "final.csv")
        matches = glob.glob(deep, recursive=True)
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


def _coerce(value: str) -> str:
    """Normalise 'no_value' / '-not available-' → empty string; strip whitespace."""
    v = value.strip() if value else ""
    return NA if v.lower() in ("no_value", "-not available-", "") else v


def _combine_wns_tns(wns: str, tns: str, fp: str = "") -> str:
    """
    Render a WNS/TNS pair (optionally with FailingPaths) as a single cell.

    Format: "wns / tns" or "wns / tns / fp" if fp is non-empty.
    Returns empty string (NA) if both WNS and TNS values are missing.
    """
    w = _coerce(wns)
    t = _coerce(tns)
    if w == NA and t == NA:
        return NA
    f = _coerce(fp) if fp else ""
    if f:
        return f"{w} / {t} / {f}"
    return f"{w} / {t}"


def _condense_pg_name(pg_name: str, pg_id: str) -> str:
    """
    Condense a path group name for column display.

    Short names (< 10 chars): return as-is.
    Long names:  return "ID:First3..Last2"  (e.g. "1:Fro..ro").
    If no ID available, just truncate.
    """
    if len(pg_name) < 10:
        return pg_name
    short = f"{pg_name[:3]}..{pg_name[-2:]}"
    if pg_id:
        return f"{pg_id}:{short}"
    return short


def _should_show(values: List[str]) -> bool:
    """
    Return True if the row should appear in the output.
    A row is suppressed when ALL trial values are empty/NA.
    """
    return any(v for v in values)


def _get_syn_stage(trial: TrialData, stage_name: str) -> Optional[SynStageData]:
    for s in trial.syn_stages:
        if s.stage_name == stage_name:
            return s
    return None


def _get_pnr_stage(trial: TrialData, stage_name: str) -> Optional[PnrStageData]:
    for s in trial.pnr_stages:
        if s.stage_name == stage_name:
            return s
    return None


def _union_stage_names(
    stage_lists: List[List[Any]], key  # key: Callable[[Any], str]
) -> List[str]:
    """
    Return an ordered union of stage names across all trials,
    preserving the order they appear in each trial's list.
    """
    seen: Dict[str, bool] = {}
    for lst in stage_lists:
        for item in lst:
            seen[key(item)] = True
    return list(seen.keys())


def _union_metric_keys(metric_dicts: List[Dict[str, str]]) -> List[str]:
    """Return ordered union of metric keys across multiple stage dicts."""
    seen: Dict[str, bool] = {}
    for d in metric_dicts:
        for k in d:
            if not k.startswith("_"):
                seen[k] = True
    return list(seen.keys())


# Fallback synthesis stage order (used when no trial data is available).
_SYN_STAGE_ORDER = ["constraints", "pre_gen", "syn_gen", "map", "final"]


def _union_syn_stage_names(
    dicts: List[Dict[str, Any]],
    preferred_order: Optional[List[str]] = None,
) -> List[str]:
    """
    Return an ordered union of stage names across multiple stage-keyed dicts
    (cell_depths or xreplay_power on TrialData).

    Ordering uses preferred_order when provided (derived from the actual CSV
    stage sequence) or falls back to the hardcoded _SYN_STAGE_ORDER list.

    Cell-depth/xreplay stage names may carry a "post_" prefix (e.g.
    "post_syn_gen") that is stripped before looking up the order position.
    Unknown names are appended alphabetically at the end.
    """
    seen: Dict[str, bool] = {}
    for d in dicts:
        for k in d:
            seen[k] = True

    order = preferred_order if preferred_order is not None else _SYN_STAGE_ORDER
    order_map = {s: i for i, s in enumerate(order)}

    def _order_key(name: str) -> Tuple[int, str]:
        # Direct match
        if name in order_map:
            return (order_map[name], name)
        # Strip common prefixes and retry
        for prefix in ("post_", "pre_"):
            stripped = name[len(prefix):] if name.startswith(prefix) else name
            if stripped in order_map:
                return (order_map[stripped], name)
        return (len(order), name)

    return sorted(seen.keys(), key=_order_key)


def _syn_fingerprint(trial: TrialData) -> str:
    """
    Create a canonical string that uniquely identifies a trial's SYN data.

    Two trials with the same fingerprint are considered to have produced
    identical synthesis results.  The fingerprint covers all stage names
    (in order) and all metric values (by column index).
    """
    if not trial.syn_stages:
        return ""
    parts: List[str] = []
    for stage in trial.syn_stages:
        parts.append(stage.stage_name)
        for col_idx in sorted(stage.metrics.keys()):
            parts.append(f"{col_idx}:{stage.metrics[col_idx]}")
    return "|".join(parts)


def _trial_age(trial: TrialData) -> float:
    """
    Return a float representing how old a trial is (smaller = older/earlier).

    Uses the mtime of the SYN log or final.csv.  Falls back to 0.0 so that
    a trial with no file metadata sorts before anything with a real mtime.
    """
    for candidate in (
        trial.syn_log_path,
        # Try final.csv location based on syn_log_path
        os.path.join(os.path.dirname(trial.syn_log_path), "..", "reports",
                     "summary_table", "final.csv") if trial.syn_log_path else "",
    ):
        if candidate and os.path.isfile(candidate):
            try:
                return os.path.getmtime(candidate)
            except OSError:
                pass
    return 0.0


def _union_bucket_labels(bucket_order_lists: List[List[str]]) -> List[str]:
    """
    Return a sorted union of cell-depth bucket labels across all trials.

    Buckets look like "1 to 5", "6 to 10", …  Sorting is done by the
    numeric start of each range so the output rows are in ascending depth order.
    Missing buckets for a trial are filled with "0 0" by the caller.
    """
    seen: Dict[str, bool] = {}
    for order in bucket_order_lists:
        for label in order:
            seen[label] = True

    def _bucket_start(label: str) -> int:
        try:
            return int(label.split()[0])
        except (ValueError, IndexError):
            return 9999

    return sorted(seen.keys(), key=_bucket_start)
