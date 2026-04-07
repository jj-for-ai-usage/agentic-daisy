"""Tool: list_tasks -- list tracked tasks with optional filtering."""

NAME = "list_tasks"
DESCRIPTION = (
    "List all tasks, optionally filtered by status or tag. "
    "Returns summaries (use get_task for full detail)."
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
