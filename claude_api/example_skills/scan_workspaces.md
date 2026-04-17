---
name: scan_workspaces
summary: Find active Cadence SYN/PNR workspaces under a project root and emit ACTIVE/SYN/PNR .rpt files
trigger: "find workspaces", "list trials", "scan EIEX", "active workspaces", "which workspaces are running", "find active trials"
---

# Scan EDA Workspaces

## When to Use
At the start of any session that involves analyzing or comparing Cadence workspaces
under a project tree (e.g. `/proj/vendor_*/EIEX_0.1`). Always run this before
`tabulate_workspaces` unless the user has already pointed at an existing
`ACTIVE_workspaces.rpt`.

For per-trial "what stage is this at?" questions, follow up with
`check_workspace_stage` on the specific workspace — `scan_workspaces` only
returns counts and the rpt file paths, not per-trial stage detail.

## Steps
1. Confirm the `search_root` with the user if it is not obvious from the
   conversation (e.g. "scan EIEX" → ask which EIEX version / project path).
   Do not guess project paths.
2. Call `scan_workspaces(search_root="...")`. Leave `analyze_stages=true` for
   the default internal stage detection (classifies active/syn/pnr correctly);
   pass `false` only when the user wants a fast count and no log reads.
3. Read the returned `counts`:
   - `active == 0` → warn user; suggest wrong path or no trials started yet.
   - `syn + pnr` large → offer to `tabulate_workspaces` next.
4. Mention the three `.rpt` file paths returned in `rpt_files`. `ACTIVE` is the
   input for `tabulate_workspaces`; `SYN` / `PNR` are reference lists.

## Key Files
- `<output_dir>/ACTIVE_workspaces.rpt` — one work-location path per line;
  exactly the list `tabulate_workspaces` expects. PNR supersedes SYN for the
  same trial root.
- `<output_dir>/SYN_workspaces.rpt` — all SYN workspaces (reference only).
- `<output_dir>/PNR_workspaces.rpt` — all PNR workspaces (reference only).

## Example Commands
```
scan_workspaces(search_root="/proj/vendor_jjohnson1/EIEX_0.1")
scan_workspaces(search_root="/proj/.../EIEX_0.1", output_dir="/tmp/scan_out", analyze_stages=false)
```

After the tool returns, quick inspect:
```
wc -l /proj/.../ACTIVE_workspaces.rpt
head /proj/.../ACTIVE_workspaces.rpt
```

## Output Format
Tell the user:
- How many ACTIVE / SYN / PNR were found. `counts.total` is the union
  (typically SYN + PNR since a trial root contributes to at most one of each).
- Path to `ACTIVE_workspaces.rpt`.
- Suggested next step (usually: run `tabulate_workspaces` against the rpt file,
  optionally with a `baseline` if they want a delta comparison).
- Any anomalies: 0 active, a huge skew (many SYN / no PNR), or tool errors.
