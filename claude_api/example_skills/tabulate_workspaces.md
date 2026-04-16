---
name: tabulate_workspaces
summary: Extract SYN/PNR metrics from EDA workspaces into a semicolon-delimited CSV (Excel-paste ready), with optional baseline deltas
trigger: "tabulate", "compare trials", "metrics for these workspaces", "tabulation.csv", "timing summary", "baseline compare", "vs baseline", "WNS TNS summary"
---

# Tabulate EDA Workspaces

## When to Use
Any time the user wants a side-by-side metrics view of two or more Cadence
workspaces: timing (WNS/TNS per path group), power (innovus / xreplay /
multi_vcd_xreplay), density, area, gate statistics, runtime, etc.
Output is a semicolon-delimited CSV designed to paste into Excel or LibreOffice.

Run after `scan_workspaces` (use its `ACTIVE_workspaces.rpt`), or when the user
explicitly lists workspace paths.

## Steps
1. Identify the input:
   - If the user just scanned, use the returned
     `rpt_files.active` as `workspace_list_file`.
   - If they listed directories inline, pass them as `work_dirs`.
   - Never pass both.
2. Listen for comparison intent ("vs baseline", "against trial X", "delta").
   If present, ask which workspace is the baseline (or infer from context) and
   pass it as `baseline`. Baseline moves to column 0 and adds three delta
   sections (PLACEOPT / CLOCKOPT / ROUTEOPT).
3. Call `tabulate_workspaces(...)`. Optional `output_file` to override the
   default (defaults to `<input_dir>/tabulation.csv`).
4. Inspect the `preview` field to sanity-check the header (look for `Title`,
   `Block Name`, `Slack`, `Stage`, and — if baseline was set — `Baseline
   Comparison`).
5. Report the path to the CSV and `num_trials`. For targeted follow-up
   questions ("what's the WNS for trialA at PLACEOPT?") use `read_file` +
   `grep` against the CSV rather than re-running tabulation.

## Key Files
- Input: an `ACTIVE_workspaces.rpt` (from `scan_workspaces`) or an inline list.
- Output: `<output_file>` — semicolon-delimited CSV. Non-numeric cells are
  prefixed with `' ` so Excel treats them as text.

## Example Commands
```
tabulate_workspaces(workspace_list_file="/proj/.../ACTIVE_workspaces.rpt")
tabulate_workspaces(workspace_list_file=".../ACTIVE_workspaces.rpt", baseline="/proj/.../trial_baseline")
tabulate_workspaces(work_dirs=["/proj/.../trialA/PNR/.../work", "/proj/.../trialB/PNR/.../work"])
```

Slice the result without re-running:
```
grep -E "^(Title|Block Name|Slack|HEPG|Density|Inn Power);" /proj/.../tabulation.csv
awk -F';' '$1 ~ /^Stage|all|HEPG|Density/' /proj/.../tabulation.csv
```

## Output Format
Tell the user:
- Path to the written CSV.
- `num_trials` and whether a baseline was applied.
- A 1–2 line interpretation of the preview (e.g. "Trial inc3_fixed has PLACEOPT
  WNS -0.105 vs baseline ISO").
- Reminder that the file is semicolon-delimited — open in Excel via
  Data → From Text with `;` separator, or paste into a `;`-configured sheet.
- If any workspace caused an extraction error, the row for that trial will say
  `block_name = ERROR`; surface that to the user.
