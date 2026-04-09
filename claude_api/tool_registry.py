"""Agentic Daisy -- Tool registration and execution."""
from __future__ import annotations

import inspect
import logging
import signal
import time
from typing import Any, Callable, Dict, List, Optional

LOG = logging.getLogger("daisy")

# Timeout for custom tool execution (seconds). Built-in tools like
# run_command/run_python enforce their own timeouts via subprocess.
TOOL_TIMEOUT = 120


class _ToolTimeoutError(Exception):
    """Raised when a tool handler exceeds TOOL_TIMEOUT."""


def _enforce_strict_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Walk an input_schema and inject ``additionalProperties: false`` and
    ``required: []`` on every object node that lacks them. Returns a new
    dict so the caller's copy is untouched. Recurses through ``properties``
    AND ``items`` (array element schemas), because Anthropic strict mode
    rejects any nested object that omits ``additionalProperties: false``.
    The ``required: []`` injection is defensive: JSON Schema treats absent
    ``required`` as equivalent to an empty list, but some strict-mode
    validators check for the key's presence explicitly. Idempotent.
    """
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if out.get("type") == "object":
        if "additionalProperties" not in out:
            out["additionalProperties"] = False
        if "required" not in out:
            out["required"] = []
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {
                k: _enforce_strict_schema(v) for k, v in props.items()
            }
    # Arrays: recurse into items (single-schema or tuple-schema form)
    items = out.get("items")
    if isinstance(items, dict):
        out["items"] = _enforce_strict_schema(items)
    elif isinstance(items, list):
        out["items"] = [_enforce_strict_schema(i) for i in items]
    # Compositors: recurse into oneOf/anyOf/allOf branches
    for key in ("oneOf", "anyOf", "allOf"):
        branches = out.get(key)
        if isinstance(branches, list):
            out[key] = [_enforce_strict_schema(b) for b in branches]
    return out


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
        """Convert to the dict expected by messages.create(tools=[...]).

        Does NOT set ``strict=True`` at the tool level. Anthropic's API
        caps strict-mode tools at 20 per request, and this framework
        ships 48+ built-in tools plus user-defined custom tools, so
        tool-level strict would break any request with more than 20
        tools loaded. Schema-level guarantees
        (``additionalProperties: false`` on every object node,
        explicit ``required`` lists) still apply via
        ``_enforce_strict_schema`` and are enough to stop Claude from
        sending unknown fields.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": _enforce_strict_schema(self.input_schema),
        }


class ToolRegistry:
    """Registry mapping tool names to handlers."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = {}
        self._scoreboard = None  # Optional ToolScoreboard; see set_scoreboard()

    def set_scoreboard(self, scoreboard) -> None:
        """Attach a ToolScoreboard. Every execute() will record stats to it.

        Scoreboard is optional: if not set, execute() behaves unchanged.
        A ToolScoreboard that raises during record() never affects the
        return value or exception of execute().
        """
        self._scoreboard = scoreboard

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

    def list_api_params(self, cache_last: bool = False) -> List[Dict[str, Any]]:
        """Return tool dicts for messages.create(tools=[...]).

        When *cache_last* is True, the last tool gets a ``cache_control``
        marker so the entire tools array is cached by the Anthropic API.
        """
        params = [t.to_api_param() for t in self._tools.values()]
        if cache_last and params:
            params[-1]["cache_control"] = {"type": "ephemeral"}
        return params

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
        start = time.time()
        had_error = False
        try:
            result = tool_def.handler(**filtered)
            return result
        except BaseException:
            had_error = True
            raise
        finally:
            if use_alarm:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            # Record usage (best-effort; never break tool execution)
            if self._scoreboard is not None:
                try:
                    elapsed_ms = int((time.time() - start) * 1000)
                    self._scoreboard.record(name, elapsed_ms, had_error)
                except Exception:
                    pass
