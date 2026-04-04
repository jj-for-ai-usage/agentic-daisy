"""Task tools — create, update, list, get tasks."""
from __future__ import annotations

from claude_api.task_store import TaskStore
from . import create_task, update_task, list_tasks, get_task

ALL_TOOLS = [create_task, update_task, list_tasks, get_task]


def register(config, registry, task_store=None, **kwargs):
    store = task_store or TaskStore(config.task_dir)
    for mod in ALL_TOOLS:
        h = mod.make_handler(task_store=store)
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
