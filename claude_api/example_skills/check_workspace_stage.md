---
name: check_workspace_stage
summary: Report where a single Cadence workspace is in the SYN/PNR flow (current stage, status, full ladder)
trigger: "what stage", "where is trial", "flow status", "is it done", "which stage", "current stage", "check progress", "status.rpt"
---

# Check Workspace Flow Stage

## When to Use
The user points at one workspace and wants to know where it is in the Cadence
flow (running, failed, done), or asks "is trial X still in PLACEOPT?" type
questions. Use after `scan_workspaces` (which gives discovery + a quick label)
when the user wants the detailed per-stage breakdown.

For surveying many workspaces at once, prefer `scan_workspaces(..., analyze_stages=true)`
— it's cheaper. This tool reads log files for all 8 stages per call.

## Steps
1. Confirm the `workspace` path — this is the work-location directory (parent
   of `syn/` and `pnr/`). `scan_workspaces` returns these in `ACTIVE_workspaces.rpt`.
2. Call `check_workspace_stage(workspace=...)`.
3. Read the summary fields first (`current_stage`, `current_status`,
   `last_completed`). 90% of the time that's all the user needs.
4. If the user wants more detail, surface the `stages[]` array (each entry has
   `name`, `status`, `mtime`, `log`). Tell them which log file is being read
   so they can grep it directly.
5. If `current_status == "FAIL"`, offer to `read_file` or `run_command` to tail
   the failing log for error messages.

## Key Files (relative to workspace)
- `syn/logs/syn.log` + `syn/reports/summary_table/final.csv` — SYN completion proof
- `pnr/<stage>/logs/<stage>.log` — per PNR stage (initdesign, floorplan,
  placeopt, clock, clockopt, route, routeopt)

## Stage Semantics
- **SUCCESS**: log has the completion marker (SYN: `Done!` in last 2 lines +
  `final.csv` newer than log + contains `final,`; PNR: `Ending` in last 2
  lines + `Finish plugin.*post.*unconditional` anywhere in log).
- **ONGOING**: log exists but no completion marker yet.
- **FAIL**: completion marker present but validation failed (SYN: csv stale
  or missing; PNR: no unconditional finish).
- **NOT_AVAILABLE**: prior stage wasn't SUCCESS, OR this log is missing, OR
  this log's mtime is older than the prior stage's (stale). Short-circuits:
  once any stage is non-SUCCESS, every later stage is NOT_AVAILABLE.

## Example Commands
```
check_workspace_stage(workspace="/proj/.../trial_A/PNR/cip_eiex/iflowblocks/cip_eiex/imp/trial_A")
```

Follow-up drilling when FAIL:
```
tail -100 <log_from_stages_array>
grep -iE "error|fatal" <log>
```

## Output Format
Tell the user, in order:
1. One-line summary: `trial X is at <current_stage> (<current_status>),
   last_completed=<last_completed>`.
2. If FAIL: the log path and suggest next step (tail/grep).
3. If they asked for detail: the 8-row ladder as a table (name, status, mtime).
4. If status is SUCCESS at ROUTEOPT, say the trial is fully done.
