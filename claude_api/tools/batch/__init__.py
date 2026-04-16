"""Batch tools — submit, check, get results."""
from __future__ import annotations

from claude_api.batch_store import BatchStore
from claude_api.task_store import TaskStore
from . import submit_batch, check_batch, get_batch_results

ALL_TOOLS = [submit_batch, check_batch, get_batch_results]


def register(config, registry, task_store=None, audit=None, **kwargs):
    batch_store = BatchStore(config.batch_dir)
    store = task_store or TaskStore(config.task_dir)
    for mod in ALL_TOOLS:
        h = mod.make_handler(
            batch_store=batch_store, task_store=store, config=config,
            audit=audit,
        )
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, h)
