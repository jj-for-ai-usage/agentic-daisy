"""Tool: repair_skills — scan, diagnose, and auto-fix skill definitions."""
import json
import os

NAME = "repair_skills"
DESCRIPTION = (
    "Scan skill directories and diagnose/fix problems with .md skill files. "
    "Default: dry-run report. Set fix=true to apply safe auto-fixes."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "fix": {
            "type": "boolean",
            "description": "Apply safe auto-fixes (default: false, dry-run only)",
        },
    },
}


def make_handler(config=None, skill_loader=None, **kwargs):
    from claude_api.config import USER_SKILLS_DIR

    def _handler(fix=False):
        # Use the same directories the SkillLoader scans
        if skill_loader is not None and hasattr(skill_loader, "skills_dirs"):
            dirs = list(skill_loader.skills_dirs)
        else:
            dirs = [USER_SKILLS_DIR]
            if config:
                dirs.append(config.skills_dir)
        results = []

        for d in dirs:
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".md"):
                    continue
                path = os.path.join(d, fname)
                entry = {
                    "file": path,
                    "issues": [],
                    "fixes_applied": [],
                    "status": "ok",
                }

                try:
                    with open(path, "r") as f:
                        content = f.read()
                except OSError as exc:
                    entry["issues"].append("Cannot read file: %s" % exc)
                    entry["status"] = "unfixable"
                    results.append(entry)
                    continue

                lines = content.split("\n")
                stem = os.path.splitext(fname)[0]
                modified = False

                # Check 1: Frontmatter exists
                has_opening = len(lines) > 0 and lines[0].strip() == "---"

                if not has_opening:
                    entry["issues"].append("Missing frontmatter (no opening ---)")
                    if fix:
                        lines = ["---", "name: %s" % stem, "summary: %s" % stem, "---", ""] + lines
                        entry["fixes_applied"].append(
                            "Added frontmatter with name/summary from filename"
                        )
                        modified = True
                else:
                    # Find closing ---
                    closing_idx = None
                    for i in range(1, len(lines)):
                        if lines[i].strip() == "---":
                            closing_idx = i
                            break

                    if closing_idx is None:
                        entry["issues"].append("Unterminated frontmatter (no closing ---)")
                        if fix:
                            # Insert closing --- after last key: value line
                            insert_at = len(lines)
                            for i in range(1, len(lines)):
                                if ":" not in lines[i] or lines[i].strip() == "":
                                    insert_at = i
                                    break
                            lines.insert(insert_at, "---")
                            closing_idx = insert_at
                            entry["fixes_applied"].append("Added closing --- delimiter")
                            modified = True

                    # Parse frontmatter keys
                    if closing_idx is not None:
                        fm_keys = {}
                        for line in lines[1:closing_idx]:
                            if ":" in line:
                                key, _, val = line.partition(":")
                                fm_keys[key.strip()] = val.strip()

                        # Check name key
                        if "name" not in fm_keys:
                            entry["issues"].append("Missing 'name' key in frontmatter")
                            if fix:
                                lines.insert(1, "name: %s" % stem)
                                closing_idx += 1
                                entry["fixes_applied"].append("Added name: %s" % stem)
                                modified = True

                        # Check summary key
                        if "summary" not in fm_keys:
                            entry["issues"].append("Missing 'summary' key in frontmatter")
                            if fix:
                                # Insert after name line (or at position 1)
                                insert_pos = 2 if ("name" in fm_keys or modified) else 1
                                lines.insert(insert_pos, "summary: %s" % stem)
                                closing_idx += 1
                                entry["fixes_applied"].append("Added summary: %s" % stem)
                                modified = True

                        # Check body content
                        body = "\n".join(lines[closing_idx + 1:]).strip()
                        if not body:
                            entry["issues"].append("Empty content body after frontmatter")
                            # NOT auto-fixable: can't generate meaningful content

                # Write fixes
                if fix and modified:
                    with open(path, "w") as f:
                        f.write("\n".join(lines))

                # Determine status
                if not entry["issues"]:
                    entry["status"] = "ok"
                elif entry["fixes_applied"] and len(entry["fixes_applied"]) >= len(entry["issues"]):
                    entry["status"] = "fixed"
                elif entry["fixes_applied"]:
                    entry["status"] = "partially_fixed"
                else:
                    entry["status"] = "needs_manual_fix"

                results.append(entry)

        # Refresh skill loader so fixes take effect
        if fix and skill_loader is not None:
            skill_loader.refresh()

        summary = {
            "total_scanned": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "fixed": sum(1 for r in results if "fixed" in r["status"]),
            "needs_manual_fix": sum(
                1 for r in results
                if r["status"] in ("unfixable", "needs_manual_fix")
            ),
            "mode": "fix" if fix else "dry_run",
        }
        return json.dumps({"summary": summary, "skills": results})

    return _handler
