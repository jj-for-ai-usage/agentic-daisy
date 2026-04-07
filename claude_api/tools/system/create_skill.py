"""Tool: create_skill -- create a new skill (playbook) .md file."""
import json
import os
import re

NAME = "create_skill"
DESCRIPTION = (
    "Create a new skill -- a step-by-step playbook (.md file) that guides "
    "you through a procedure. Saves to ~/.daisy/skills/ (persistent) by "
    "default, or .daisy/skills/ (project-level)."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name in snake_case (e.g. check_synthesis_run)",
        },
        "summary": {
            "type": "string",
            "description": "One-line description (shown in the skill index)",
        },
        "trigger": {
            "type": "string",
            "description": "Comma-separated trigger phrases",
        },
        "content": {
            "type": "string",
            "description": "Full markdown body (steps, key files, example commands, output format)",
        },
        "location": {
            "type": "string",
            "enum": ["user", "project"],
            "description": "Where to save: 'user' (~/.daisy/skills/, default) or 'project' (.daisy/skills/)",
        },
    },
    "required": ["name", "summary", "content"],
}


def _yaml_safe(val: str) -> str:
    """Quote a YAML value if it contains special characters."""
    if any(c in val for c in ':"\'\\#\n{}[]|>&!%@'):
        return '"%s"' % val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
    return val


def make_handler(config=None, skill_loader=None, **kwargs):
    from claude_api.config import USER_SKILLS_DIR

    def _handler(name, summary, content, trigger="", location="user"):
        # Sanitize name
        safe_name = re.sub(r"[^a-z0-9_]", "_", name.lower())
        # Resolve target directory
        if location == "project":
            target_dir = config.skills_dir if config else USER_SKILLS_DIR
        else:
            target_dir = USER_SKILLS_DIR
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, safe_name + ".md")
        # Build file content with frontmatter
        lines = [
            "---",
            "name: %s" % _yaml_safe(safe_name),
            "summary: %s" % _yaml_safe(summary),
        ]
        if trigger:
            lines.append("trigger: %s" % _yaml_safe(trigger))
        lines.append("---")
        lines.append("")
        lines.append(content)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        # Refresh the skill index so it's available immediately
        if skill_loader is not None:
            skill_loader.refresh()
        return json.dumps({
            "status": "created",
            "name": safe_name,
            "path": path,
            "location": location,
        })

    return _handler
