"""Tool: create_task -- create a new tracked task."""

NAME = "create_task"
DESCRIPTION = (
    "Create a new task for multi-session tracking. Tasks persist across sessions "
    "and are checked at conversation start. Use for work that spans multiple sessions."
)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string",
            "description": "Short task name (e.g. 'Optimize timing for block_X')",
        },
        "description": {
            "type": "string",
            "description": "Detailed description of what needs to be done",
        },
        "priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Task priority (default: medium)",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags for categorization (e.g. ['timing', 'block_x'])",
        },
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "active", "done", "skipped"]},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
            },
            "description": "Ordered list of subtasks / steps",
        },
        "context": {
            "type": "string",
            "description": "Key context (file paths, commands, constraints) a future session needs",
        },
    },
    "required": ["name"],
}


def make_handler(task_store=None, **kwargs):
    def _handler(name, description="", priority="medium", tags=None,
                 subtasks=None, context=""):
        return task_store.create_task(
            name=name, description=description, priority=priority,
            tags=tags, subtasks=subtasks, context=context,
        )
    return _handler
