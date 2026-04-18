---
name: compare_workspaces
summary: AE-perspective comparison of two (or more) Cadence workspaces — what changed, why, and what to investigate
trigger: "compare", "compare it to", "vs", "versus", "delta", "what changed", "why is X different", "two work directories", "two trials", "trial vs trial", "before and after", "side by side", "diff trials", "difference between"
---

# Compare EDA Workspaces (AE Perspective)

## When to Use
The user names two (or more) workspace paths and asks you to compare them.
Trigger phrases: "compare it to", "vs", "delta", "what changed", "why is X
different", "diff these trials". This is the **default entry point** for any
two-workspace question — do not start with raw `find` / `grep` / `tail`.

## Audience: AE, Not Designer
You are reporting to a Cadence Application Engineer. They do **not** care
whether either trial closes timing. They care about:

1. **What changed** between the two workspaces (metric deltas, stage status,
   runtime, configuration).
2. **Why it changed** — provided the evidence supports a conclusion.
3. **What can be learned** that applies to future trials.
4. **What needs to be fixed** in the flow, scripts, or constraints.

Do NOT:
- Declare a trial "good" or "bad" based on absolute slack/TNS.
- Recommend constraint relaxation, utilization changes, or hold-buffer
  insertion. Those are designer decisions, not AE decisions.
- Use language like "CATASTROPHIC", "CAUTIONARY TALE", "Design is NOT
  recoverable", "next 4 hours are critical". Stay neutral and factual.

## Steps

### 1. Identify the two workspace roots
The user typically gives two paths. Each path may be:
- A trial root (`/proj/.../trials/t_032/`) — contains `SYN/`, `PNR/`, `logs/`.
- A work-location dir (`/proj/.../PNR/<block>/iflowblocks/<block>/imp/<run>`) —
  the directory that contains `syn/` and `pnr/` subdirs.

If the user gave a trial root, the work-location is usually
`<root>/PNR/<block>/iflowblocks/<block>/imp/<run>`. Confirm with one
`stat_file` or `directory_tree` call (depth 2) — do **not** go on a
`find` expedition.

### 2. Run `tabulate_workspaces` FIRST — before any other reads
This is the canonical comparison tool and it does the heavy lifting in one
call. It reads SYN `final.csv`, PNR per-stage QoR, runtimes, and emits a
side-by-side semicolon CSV.

```
tabulate_workspaces(
    work_dirs=["<work_location_A>", "<work_location_B>"],
    baseline="<work_location_A>",   # the "before" / reference trial
)
```

The `baseline` argument adds explicit PLACEOPT / CLOCKOPT / ROUTEOPT delta
sections — this is what answers "what changed".

If `extraction_errors_count > 0`, surface that first; partial data means
your comparison is incomplete.

### 3. Run `check_workspace_stage` on each workspace
One call per workspace. Get `current_stage`, `current_status`,
`last_completed`, per-stage `runtime`, `current_log_tail`, and
`current_log_tail_status`.

This tells you:
- Are they at comparable stages? (Comparing a mid-PLACEOPT trial to a
  finished ROUTEOPT trial is apples-to-oranges — say so.)
- Is one of them actually stalled? See "Stale-log detection" below.

### 4. Read the tabulation CSV
```
read_file(path="<output_file from step 2>")
```
or for large CSVs:
```
run_command("grep -E '^(Title|Block Name|Stage|Slack|TNS|HEPG|Density|Inn Power|runtime);' <csv>")
```

### 5. Build the AE-perspective report (see "Output Format" below)
Lead with the delta table. Then:
- Stage-status diff (which stages did each reach? which is further along?)
- Runtime diff (where did the time go differently?)
- Metric deltas worth flagging
- Configuration / setup diffs you noticed in the tail or `final.csv`
- Open questions / next investigations

## Stale-Log Detection (Mandatory)

When a stage shows `current_status == ONGOING`, you MUST verify the log is
actually being written before describing the stage as active. Compare the
log mtime to "now":

```
stat_file(path="<current_log_path>")     # returns mtime
run_command("date +%s")                  # current epoch
```

Decision rule:
- **mtime within last 5 min** → "actively running"
- **mtime 5–60 min old** → "ONGOING per status, but no log writes for N min
  — may be in a quiet phase or stalled; flag for the user"
- **mtime > 60 min old** → "log has not been written in N min — almost
  certainly stalled, killed, or finished without the completion marker.
  Do NOT call this 'in progress'."

## Anti-Fabrication Rules (Mandatory)

Every numeric or factual claim in your final report MUST trace to a specific
tool result that the user can scroll back and find. If a value is not in a
tool result, you have two choices:

1. Run the tool that retrieves it.
2. Write "not retrieved" or "not found" — never invent a number.

Specifically forbidden:
- Inventing TNS / WNS / violation counts when the report file was not
  successfully read.
- Inventing workspace sizes (run `du -sh` if you need them).
- Inventing iteration counts, runtimes, or cell counts.
- Suppressing stderr with `2>/dev/null` during investigation — if a `find`
  returns nothing, you need to know that, not paper over it.

If `grep`/`find` returned no output, your next sentence is "report X was
not found at the expected path" — not a confidently-quoted number.

## Output Format

Lead with a small table. Example shape:

```
                        | Trial A (t_032)         | Trial B (inc_psim_wa)
Furthest stage          | placeopt (ONGOING/stalled) | routeopt (SUCCESS)
SYN runtime             | 4h 12m                  | 4h 38m
PNR runtime so far      | 18h 24m                 | 31h 02m
PLACEOPT WNS            | -0.420 ns               | -0.385 ns   (Δ +0.035)
PLACEOPT TNS            | -142.7 ns               | -119.4 ns   (Δ +23.3)
ROUTEOPT WNS            | n/a (not reached)       | -0.428 ns
... (only metrics actually retrieved)
```

Then 3–6 short bullets:
- **What's different**: state the deltas in plain words.
- **Where to look first**: which stage logs / reports show the divergence.
- **Open questions**: what data would resolve "why" — call those out as
  questions, not as conclusions.
- **Possible flow/setup issues** (if evidence supports it): config files
  that differ, missing reports, stale CSVs, runtime anomalies.

Close with: "Saved comparison CSV to `<path>`."

Do NOT close with a multi-page executive summary. Do NOT `cat > /tmp/...`
a heredoc dump of your own analysis — your previous turn already contains
it; rewriting it just burns tokens.

## Gotcha: baseline work-location can be a trial root

If `tabulate_workspaces` returns no SYN data for one of the paths, do
NOT conclude "this workspace lacks a final.csv". The user often points
at a trial root whose SYN data is nested under
`PNR/<block>/iflowblocks/<block>/imp/<run>/syn/reports/final.csv` — five
directories deeper than the canonical `<root>/syn/reports/` layout. The
tabulator deep-searches as a fallback now, but if it still comes back
empty, run:

```
run_command("find <path> -name final.csv -path '*/syn/reports/*'")
```

before giving up. Then either point the tabulator at the deeper path or
symlink-resolve the expected location. NEVER tell the user that
`tabulate_workspaces` "only handles PNR" or "doesn't support synthesis"
— it parses both SYN `final.csv` (via `_parse_syn_csv`) and PNR
per-stage QoR from the same call.

## Do not create a duplicate tool

If the first tabulate call produces thin results, the answer is NOT to
`create_tool` a parallel Genus-CSV comparator. The tabulator already
reads SYN `final.csv` and emits per-metric deltas. Creating a narrower
second tool doubles maintenance for the AE. Fix the input path or the
tabulator — don't route around it.

## What NOT to Do (Lessons from Past Sessions)

- Do not start with `directory_tree` + `find` + `grep` exploring for
  reports when `tabulate_workspaces` would extract them in one call.
- Do not call PLACEOPT "ONGOING" without checking the log mtime.
- Do not generate a "root cause analysis" section when you only ran
  `head`, `tail`, and a `grep` — those tools cannot establish causation.
- Do not echo a giant heredoc summary at the end of the session. The
  comparison CSV and your bulleted report are the deliverable.
- Do not save 3 separate `save_memory` entries for the same comparison —
  one is enough.
