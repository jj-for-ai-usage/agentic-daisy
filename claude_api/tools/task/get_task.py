"""Tool: get_task — get full detail of a specific task."""

NAME = "get_task"
DESCRIPTION = (
    "Get complete task detail including all subtasks, context, and notes. "
    "Use after list_tasks to dive into a specific task."
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
