"""Tool: get_task — get full detail of a specific task."""

NAME = "get_task"
DESCRIPTION = (
    "Get complete detail for one task: full context paragraph, every "
    "subtask with its status + notes, and the running notes history. "
    "Use when resuming a task shown in the session-start active-tasks "
    "list (you already see id/name/priority there — this fetches the "
    "rest). Task IDs look like 'task_20260404_001'."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Task ID (e.g. 'task_20260404_001')",
        },
    },
    "required": ["task_id"],
}


def make_handler(task_store=None, **kwargs):
    def _handler(task_id):
        return task_store.get_task(task_id=task_id)
    return _handler
