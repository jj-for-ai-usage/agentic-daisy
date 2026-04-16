"""Tool: list_tasks — list tracked tasks with optional filtering."""

NAME = "list_tasks"
DESCRIPTION = (
    "List tasks with optional status/tag filters. Returns summaries only "
    "(id, name, priority, subtask count, updated-at). For full detail "
    "including subtasks + notes + context, follow up with get_task. "
    "Note: active tasks are ALREADY injected into the system prompt at "
    "session start — only call this tool when you need tasks outside the "
    "'active' status (e.g. list_tasks(status='blocked') to find what's "
    "waiting on batch results) or a tag-filtered view."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["active", "paused", "done", "blocked"],
            "description": "Filter by status (omit for all tasks)",
        },
        "tag": {
            "type": "string",
            "description": "Filter by tag",
        },
    },
}


def make_handler(task_store=None, **kwargs):
    def _handler(status=None, tag=None):
        return task_store.list_tasks(status=status, tag=tag)
    return _handler
