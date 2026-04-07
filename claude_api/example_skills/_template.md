---
name: my_skill_name
summary: One-line description shown in the skill index (keep under 100 chars)
trigger: "phrases that should trigger this skill", "alternate phrasing"
---

# My Skill Name

## When to Use
Describe what situations this skill applies to.

## Steps
1. First thing to check -- which file, what command, what to look for
2. Second step -- how to interpret the result from step 1
3. Third step -- deeper investigation if needed
4. Continue as needed...

## Key Files
- path/to/important/file.log -- what this file contains
- path/to/another/file.rpt -- when to check this

## Example Commands
```
tail -30 path/to/file.log
grep -i error path/to/file.log | head -20
```

## Output Format
Describe what the user should see in the final response:
- Status (running/done/failed)
- Key metrics or findings
- Any errors or warnings
- Suggested next steps
