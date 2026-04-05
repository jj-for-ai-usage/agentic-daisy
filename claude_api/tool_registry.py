"""Agentic Daisy — Tool registration and execution."""
from __future__ import annotations

import inspect
import logging
import signal
from typing import Any, Callable, Dict, List, Optional

LOG = logging.getLogger("daisy")

# Timeout for custom tool execution (seconds). Built-in tools like
# run_command/run_python enforce their own timeouts via subprocess.
TOOL_TIMEOUT = 120


class _ToolTimeoutError(Exception):
    """Raised when a tool handler exceeds TOOL_TIMEOUT."""


class ToolDef:
    """Definition of a single tool that Claude can call."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., str],
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_api_param(self) -> Dict[str, Any]:
        """Convert to the dict expected by messages.create(tools=[...])."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Registry mapping tool names to handlers."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., str],
    ) -> None:
        # Anthropic API requires input_schema to have "type" field
        if not isinstance(input_schema, dict) or "type" not in input_schema:
            LOG.warning(
                "Tool '%s' has invalid input_schema (missing 'type'). "
                "Auto-fixing to type: object.", name,
            )
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object"}
            else:
                input_schema = dict(input_schema, type="object")
        self._tools[name] = ToolDef(name, description, input_schema, handler)

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_api_params(self) -> List[Dict[str, Any]]:
        return [t.to_api_param() for t in self._tools.values()]

    def list_tool_summaries(self) -> List[Dict[str, str]]:
        """Return [{name, description}, ...] for building system prompts."""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]

    def has_tools(self) -> bool:
        return len(self._tools) > 0

    def list_names(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def execute(self, name: str, input_args: Dict[str, Any]) -> str:
        """Execute a tool by name. Returns result as string."""
        tool_def = self._tools[name]
        # Filter to only params the handler accepts, so extra fields
        # from Claude don't cause TypeError.
        sig = inspect.signature(tool_def.handler)
        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            filtered = input_args  # handler accepts **kwargs
        else:
            accepted = {p.name for p in params.values()}
            filtered = {k: v for k, v in input_args.items() if k in accepted}

        # Apply signal-based timeout (Unix main thread only)
        use_alarm = hasattr(signal, "SIGALRM")
        if use_alarm:
            def _alarm_handler(signum, frame):
                raise _ToolTimeoutError(
                    "Tool '%s' timed out after %ds" % (name, TOOL_TIMEOUT)
                )
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(TOOL_TIMEOUT)
        try:
            return tool_def.handler(**filtered)
        finally:
            if use_alarm:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
