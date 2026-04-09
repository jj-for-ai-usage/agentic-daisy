"""Tool: list_tools -- introspect the currently-registered tool catalog.

Returns full input schemas (verbose mode) so the agent can inspect a
tool's signature before calling it. Useful for:
  - Answering "what can you do?" reliably mid-session
  - Avoiding name collisions when calling create_tool
  - Recovering from a forgotten schema without re-reading the system prompt
"""
from __future__ import annotations

import json

NAME = "list_tools"
DESCRIPTION = (
    "List all currently-registered tools with their full input schemas. "
    "Optional name_filter is a substring match on tool names."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name_filter": {
            "type": "string",
            "description": "Optional substring filter; only tools whose name contains this are returned.",
        },
    },
}


def make_handler(registry=None, **kwargs):
    def _handler(name_filter: str = "") -> str:
        if registry is None:
            return json.dumps({"error": "registry not available"})
        out = []
        for name in registry.list_names():
            if name_filter and name_filter not in name:
                continue
            tool = registry.get(name)
            if tool is None:
                continue
            out.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            })
        return json.dumps({"count": len(out), "tools": out})

    return _handler
