"""Tool: plan_write -- ephemeral in-session planning.

Stores a flat list of plan steps shared between plan_write and plan_show
via a session-scoped Planner object (see system/__init__.py). The plan
dies when the session ends; it is NEVER persisted to disk. For durable,
multi-session work, use create_task / update_task instead.

Usage pattern (matches Claude Code's TodoWrite): the agent passes the
FULL plan on every call; the previous state is replaced. Exactly one
step can be 'in_progress' at a time.
"""
from __future__ import annotations

import json

NAME = "plan_write"
DESCRIPTION = (
    "Create or replace the in-session plan. Pass the FULL list of steps "
    "each call; the previous plan is replaced. Use this for multi-step "
    "requests (3+ steps) to commit to a plan before touching other tools. "
    "The plan is in-memory only: it dies at session end. For durable "
    "multi-session work use create_task instead. Exactly one step may be "
    "'in_progress' at a time. Returns the rendered plan."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": (
                "Full replacement list of plan steps. Each step is a "
                "{content, status} object. Pass an empty list to clear "
                "the plan."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "What needs to be done (short, imperative).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "Current status of this step.",
                    },
                },
                "required": ["content", "status"],
            },
        },
    },
    "required": ["steps"],
}


def make_handler(planner=None, **kwargs):
    def _handler(steps):
        if planner is None:
            return json.dumps({"error": "planner not available"})
        # Validate: at most one in_progress
        in_progress = [s for s in steps if s.get("status") == "in_progress"]
        if len(in_progress) > 1:
            return json.dumps({
                "error": "at most one step may be 'in_progress' at a time",
                "in_progress_count": len(in_progress),
            })
        # Validate each step has content + status
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                return json.dumps({"error": "step %d is not an object" % i})
            if not s.get("content"):
                return json.dumps({"error": "step %d missing 'content'" % i})
            if s.get("status") not in ("pending", "in_progress", "completed"):
                return json.dumps({
                    "error": "step %d has invalid 'status': %r" % (i, s.get("status"))
                })
        planner.replace(steps)
        return json.dumps({
            "status": "updated",
            "step_count": len(steps),
            "pending": sum(1 for s in steps if s["status"] == "pending"),
            "in_progress": sum(1 for s in steps if s["status"] == "in_progress"),
            "completed": sum(1 for s in steps if s["status"] == "completed"),
            "plan": steps,
        })
    return _handler
