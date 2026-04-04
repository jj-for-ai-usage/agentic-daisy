"""Agentic Daisy — Core agentic conversation loop."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import anthropic

from .audit import AuditLogger
from .config import DaisyConfig
from .tool_registry import ToolRegistry

LOG = logging.getLogger("daisy")

MAX_TOOL_ROUNDS = 20


def run_agent_loop(
    config: DaisyConfig,
    user_message: str,
    registry: ToolRegistry,
    audit: AuditLogger,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Run a full agentic conversation starting from *user_message*.

    Returns the final text response from Claude.
    Mutates *conversation_history* in place so the caller can reuse it
    for multi-turn interactive sessions.
    """
    client = anthropic.Anthropic(api_key=config.api_key)

    if conversation_history is None:
        conversation_history = []

    conversation_history.append({"role": "user", "content": user_message})

    # Build kwargs for the API call
    sys_prompt = system_prompt or config.system_prompt or ""

    for round_num in range(MAX_TOOL_ROUNDS):
        call_kwargs: Dict[str, Any] = dict(
            model=config.model,
            max_tokens=config.max_tokens,
            messages=conversation_history,
        )
        if sys_prompt:
            call_kwargs["system"] = sys_prompt
        if registry.has_tools():
            call_kwargs["tools"] = registry.list_api_params()

        LOG.debug("Round %d: sending request to %s", round_num, config.model)
        t0 = time.time()
        response = client.messages.create(**call_kwargs)
        elapsed = time.time() - t0

        audit.log_api_call(
            model=config.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            latency_s=elapsed,
            round_num=round_num,
        )
        LOG.debug(
            "Round %d: %d input / %d output tokens, stop=%s (%.2fs)",
            round_num,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.stop_reason,
            elapsed,
        )

        # Serialize assistant content blocks to plain dicts
        assistant_content: List[Dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        conversation_history.append({"role": "assistant", "content": assistant_content})

        # If Claude is done (no tool calls), return the final text
        if response.stop_reason != "tool_use":
            parts = []
            for block in response.content:
                if block.type == "text":
                    parts.append(block.text)
            return "\n".join(parts) if parts else ""

        # Execute each tool call and collect results
        tool_results: List[Dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            LOG.debug("Executing tool: %s(%s)", tool_name, tool_input)

            t_tool = time.time()
            try:
                result_str = registry.execute(tool_name, tool_input)
                is_error = False
            except KeyError:
                result_str = "Error: unknown tool '%s'" % tool_name
                is_error = True
            except Exception as exc:
                result_str = "Error executing %s: %s" % (tool_name, exc)
                is_error = True

            tool_elapsed = time.time() - t_tool
            audit.log_tool_execution(
                tool_name=tool_name,
                success=not is_error,
                latency_s=tool_elapsed,
                round_num=round_num,
            )

            if is_error:
                LOG.warning("Tool %s failed: %s", tool_name, result_str)
            else:
                LOG.debug("Tool %s completed in %.3fs", tool_name, tool_elapsed)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
                "is_error": is_error,
            })

        conversation_history.append({"role": "user", "content": tool_results})

    return "[Daisy: max tool rounds (%d) reached, stopping]" % MAX_TOOL_ROUNDS
