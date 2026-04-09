"""Tool: plan_show -- read the in-session plan.

Returns whatever plan_write last stored in the session-scoped Planner.
Returns an empty list if no plan has been set. See plan_write for details.
"""
from __future__ import annotations

import json

NAME = "plan_show"
DESCRIPTION = (
    "Return the current in-session plan (set via plan_write). Returns an "
    "empty list if no plan has been set this session. Cheap to call; use "
    "it to remind yourself where you are in a multi-step workflow."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
}


def make_handler(planner=None, **kwargs):
    def _handler():
        if planner is None:
            return json.dumps({"error": "planner not available"})
        steps = planner.get()
        return json.dumps({
            "step_count": len(steps),
            "pending": sum(1 for s in steps if s["status"] == "pending"),
            "in_progress": sum(1 for s in steps if s["status"] == "in_progress"),
            "completed": sum(1 for s in steps if s["status"] == "completed"),
            "plan": steps,
        })
    return _handler
