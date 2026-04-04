"""Tool: load_skill — load a domain skill/playbook by name."""
NAME = "load_skill"
DESCRIPTION = (
    "Load a domain skill (playbook) by name to get the full step-by-step "
    "procedure. Check the available skills list in your instructions first."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name from the available skills list",
        },
    },
    "required": ["name"],
}


def make_handler(skill_loader=None, **kwargs):
    return skill_loader.load_skill
