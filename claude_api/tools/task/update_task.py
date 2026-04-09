"""Tool: update_task -- update an existing task's status, notes, or subtasks."""

NAME = "update_task"
DESCRIPTION = (
    "Update a task's status, add progress notes, add/update subtasks. "
    "Use to track progress across sessions."
)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Task ID (e.g. 'task_20260404_001')",
        },
        "status": {
            "type": "string",
            "enum": ["active", "paused", "done", "blocked"],
            "description": "New task status",
        },
        "notes": {
            "type": "string",
            "description": "Progress notes (appended to context)",
        },
        "context": {
            "type": "string",
            "description": "Replace full context (use notes to append instead)",
        },
        "add_subtask": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "active", "done", "skipped"]},
                "notes": {"type": "string"},
            },
            "required": ["name"],
            "description": "Add a new subtask",
        },
        "update_subtask": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "index": {"type": "integer", "description": "0-based subtask index"},
                "status": {"type": "string", "enum": ["pending", "active", "done", "skipped"]},
                "notes": {"type": "string"},
            },
            "required": ["index"],
            "description": "Update an existing subtask by index",
        },
    },
    "required": ["task_id"],
}


def make_handler(task_store=None, **kwargs):
    def _handler(task_id, status=None, notes=None, context=None,
                 add_subtask=None, update_subtask=None):
        return task_store.update_task(
            task_id=task_id, status=status, notes=notes, context=context,
            add_subtask=add_subtask, update_subtask=update_subtask,
        )
    return _handler
