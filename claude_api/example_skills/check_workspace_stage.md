---
name: check_workspace_stage
summary: Report current SYN/PNR stage of one workspace, with sub-stages, runtimes, and current-log tail
trigger: "what stage", "where is trial", "flow status", "is it done", "which stage", "current stage", "check progress", "status.rpt", "syn substages", "runtime", "is it stuck"
---

# Check Workspace Flow Stage

## When to Use
The user points at one workspace and wants to know where it is in the Cadence
flow (running, failed, done), how long each completed stage took, or asks
"is trial X still in PLACEOPT?"-type questions. Use after `scan_workspaces`
(which gives discovery + a quick label) when the user wants the detailed
per-stage breakdown.

For surveying many workspaces at once, prefer `scan_workspaces(..., analyze_stages=true)`
— it's cheaper. This tool reads log files for all 8 stages plus parses the
SYN CSV plus tails one log.

## Steps
1. Confirm the `workspace` path — this is the work-location directory (parent
   of `syn/` and `pnr/`). `scan_workspaces` returns these in `ACTIVE_workspaces.rpt`.
2. Call `check_workspace_stage(workspace=...)`.
3. Read the summary fields first: `current_stage`, `current_status`,
   `last_completed`. 90% of the time that's the answer.
4. **Use the `current_log_tail` as a sanity check.** If the tool says SUCCESS
   but the tail shows suspicious warnings/errors, flag them. If it says
   ONGOING, the tail shows what the trial is doing right now. If it says
   FAIL, the tail usually contains the error.
5. If the user wants more detail, surface:
   - `stages[]`: per-stage `status`, `mtime`, `runtime` (h:m:s)
   - `stages[0].syn_substages[]`: which SYN sub-stages completed and
     per-substage `real_runtime`
   - `stages[0].final_csv_mtime`: lets you spot stale CSVs even when
     the tool validated SUCCESS
6. For follow-up error hunting, `read_file` or `grep` the log at
   `current_log_path` (or any stage's `log` field).

## Key Files (relative to workspace)
- `syn/logs/syn.log` — SYN completion marker (`Done!` in last 2 lines)
- `syn/reports/summary_table/final.csv` — SYN sub-stage metrics + runtimes
- `pnr/<stage>/logs/<stage>.log` — per PNR stage (initdesign, floorplan,
  placeopt, clock, clockopt, route, routeopt). Completion: `Ending` in
  last 2 lines + `Finish plugin.*post.*unconditional` anywhere. Runtime:
  parsed from `real=HH:MM:SS` in the Innovus `Ending` line.

## Stage Semantics
- **SUCCESS**: log has the completion marker; for SYN also `final.csv`
  newer than `syn.log` and contains a `final,` row.
- **ONGOING**: log exists but no completion marker yet.
- **FAIL**: completion marker present but validation failed (SYN: csv
  stale/missing; PNR: no unconditional finish).
- **NOT_AVAILABLE**: prior stage wasn't SUCCESS, OR this log is missing,
  OR this log's mtime is older than the prior stage's. Short-circuits:
  once any stage is non-SUCCESS, every later stage is NOT_AVAILABLE.

## SYN Sub-stages
`syn_substages[]` appears ONLY on `stages[0]` (the SYN entry); PNR stage
entries (`stages[1]` through `stages[7]`) do not carry this field. It is
parsed from `syn/reports/summary_table/final.csv` in source order. Typical
sequence: `constraints → pre_gen → syn_gen → map → multibit → exportnonscan
→ scan → post_scan_opt → finalincropt → final`.
**Presence of a row means that sub-stage ran to completion** (it emitted
metrics). Each entry gives `real_runtime` (per-substage wall time) and
`real_elapsed` (cumulative since SYN start). If `final` is in the list
and `current_status == SUCCESS`, SYN is fully done; if `final` is absent,
SYN got through the last-listed sub-stage and stopped.

## Runtime
- **SYN top-level `runtime`**: last CSV row's `real_elapsed` (total SYN
  wall time).
- **PNR per-stage `runtime`**: last `real=HH:MM:SS` occurrence in the
  stage log (only populated for SUCCESS stages).
- `runtime: null` when the stage isn't SUCCESS (except SYN which can
  still show a runtime if a CSV was written).

## Stale-Log Detection (MANDATORY for ONGOING)

`current_status == ONGOING` only means the log lacks a completion marker.
It does NOT mean the tool is still writing. Before describing an ONGOING
stage as "active" or "in progress", verify the log is being written:

```
stat_file(path=<current_log_path>)   # returns mtime
run_command("date +%s")              # current epoch
```

Decision rule based on `now - mtime`:
- **< 5 minutes** -> "actively running" is OK to say.
- **5 - 60 minutes** -> "ONGOING per the log marker, but no writes for N min
  -- may be in a quiet phase or stalled". State the age explicitly.
- **> 60 minutes** -> Do NOT say "in progress". Say "log has not been
  written in N minutes; almost certainly stalled, killed, or finished
  without the completion marker. Needs a process check (`ps`/`qstat`) to
  confirm."

The `final.csv` mtime check (`final_csv_mtime`) is the SYN-stage analog --
a fresh `syn.log` with a stale csv is a partial / aborted run.

## Current-log Tail
- `current_log_path` is the log file of `current_stage`; `current_log_tail`
  is its last ~40 lines, hard-capped at 4000 characters INCLUDING the
  truncation marker `\n... (truncated)` appended when the cap is hit. Very
  long log lines may be cut mid-line; don't rely on exact line counts.
- `current_log_tail_status` tells you *why* the tail might be missing:
  - `"ok"` — tail read successfully.
  - `"empty"` — log file exists but is zero bytes.
  - `"not_found"` — no current log (usually paired with `NOT_STARTED`).
  - `"not_started"` — no stage has a log yet.
  - `"read_error"` — log exists but couldn't be read (permissions, I/O); a
    matching message is appended to `warnings[]`. Surface this to the user.
- **Always skim the tail** — the rule-based checks can miss panic strings,
  license errors, or oddities outside the regex patterns. If the tail shows
  content that contradicts the tool's status (e.g. status=SUCCESS but tail
  shows "license expired"), surface the disagreement to the user verbatim.

## Fail Reasons
Each stage carries a `fail_reason` field that is non-null only when the
stage status is `FAIL`. Use it to tell the user WHY the stage failed:
- SYN: `"csv_missing"` (Done! but no final.csv), `"csv_stale"` (csv mtime
  predates syn.log — prior-run leftover), `"no_final_row"` (csv exists and
  is fresh but lacks a `final,` row — synthesis didn't reach final).
- PNR: `"no_unconditional_finish"` — log has `Ending` but lacks the
  unconditional-finish marker; Innovus exited abnormally.

## Warnings
`warnings[]` is a list of short human-readable strings describing silent
errors the tool detected. Non-empty when:
- `final.csv` exists but couldn't be parsed (row width, encoding, etc.).
- Current log tail couldn't be read (permissions, stale NFS).
**Always relay warnings verbatim to the user** — the tool state may look
nominal but the data the agent is reasoning about is incomplete.

## Example Commands
```
check_workspace_stage(workspace="/proj/.../trial_A/PNR/cip_eiex/iflowblocks/cip_eiex/imp/trial_A")
```

Follow-up drilling when FAIL or suspicious tail:
```
tail -200 <current_log_path>
grep -iE "error|fatal|panic|license" <current_log_path>
read_file(path=<current_log_path>, start_line=<N>, num_lines=100)
```

## Output Format
Tell the user, in order:
1. One-line summary: `trial X is at <current_stage> (<current_status>),
   last_completed=<last_completed>`.
2. SYN detail if relevant: `SYN completed N sub-stages; last was
   <syn_substages[-1].name> (runtime <real_elapsed>)`.
3. Per-stage runtimes for completed stages if the user is comparing speed.
4. Tail summary: either "tail looks clean" or surface the suspicious
   lines verbatim (2–5 lines). If the tool's status disagrees with what
   the tail shows, flag the disagreement.
5. If FAIL or ONGOING: suggest a next step (grep for error, watch log,
   wait and re-check).
6. If everything is SUCCESS through ROUTEOPT, say the trial is fully done.
