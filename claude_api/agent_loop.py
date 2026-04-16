"""Agentic Daisy — Core agentic conversation loop."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import anthropic

from .audit import AuditLogger, BudgetExceededError
from .config import DaisyConfig
from .tool_registry import ToolRegistry

LOG = logging.getLogger("daisy")

MAX_TOOL_ROUNDS = 20
MAX_API_RETRIES = 4
RETRY_BASE_DELAY = 2  # seconds; doubles each retry: 2, 4, 8, 16

# Rough per-message token budget to warn before overflow.
# Leave headroom for the response; warn if input approaches this.
CONTEXT_TOKEN_WARNING = 150_000

# Max chars for a tool result before spilling to file.
# ~12,500 tokens — keeps individual results safe even with many rounds.
MAX_TOOL_RESULT = 50_000

# Tools whose results can be cached within a single agent loop (read-only tools)
_CACHEABLE_TOOLS = frozenset({
    "read_file", "search_files", "find_files", "list_directory",
    "directory_tree", "search_memory", "list_memories", "get_env",
    "load_skill", "list_tasks", "get_task",
    "get_batch_results",
})


def _call_api_with_retry(client, call_kwargs: Dict[str, Any]) -> Any:
    """Call messages.create with exponential backoff on transient errors."""
    last_exc = None
    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return client.messages.create(**call_kwargs)
        except anthropic.RateLimitError as exc:
            last_exc = exc
            if attempt == MAX_API_RETRIES:
                break
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            LOG.warning(
                "Rate limited (attempt %d/%d), retrying in %ds...",
                attempt + 1, MAX_API_RETRIES + 1, delay,
            )
            time.sleep(delay)
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            if attempt == MAX_API_RETRIES:
                break
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            LOG.warning(
                "Connection error (attempt %d/%d): %s — retrying in %ds...",
                attempt + 1, MAX_API_RETRIES + 1, exc, delay,
            )
            time.sleep(delay)
        except anthropic.APIStatusError as exc:
            # Retry on 500/502/503/529 (overloaded); don't retry 400/401/403
            if exc.status_code in (500, 502, 503, 529):
                last_exc = exc
                if attempt == MAX_API_RETRIES:
                    break
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                LOG.warning(
                    "API error %d (attempt %d/%d), retrying in %ds...",
                    exc.status_code, attempt + 1, MAX_API_RETRIES + 1, delay,
                )
                time.sleep(delay)
            else:
                raise  # 400, 401, 403 etc. — not transient
    raise last_exc  # type: ignore[misc]


def _spill_to_file(content: str, tool_name: str, config) -> str:
    """Save oversized tool output to a workspace file, return the path."""
    workspace = getattr(config, "workspace_dir", None)
    if not workspace:
        raise RuntimeError(
            "Cannot spill tool output: config.workspace_dir is not set"
        )
    os.makedirs(workspace, mode=0o700, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = "tool_%s_%s.txt" % (tool_name, ts)
    path = os.path.join(workspace, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def _make_cache_key(tool_name: str, tool_input: dict) -> str:
    """Deterministic cache key for a tool call."""
    return tool_name + ":" + json.dumps(tool_input, sort_keys=True)


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

    # Per-turn tool result cache (read-only tools only)
    tool_cache: Dict[str, str] = {}

    # Conversation compactor (lazy import to avoid circular deps)
    from .compaction import ConversationCompactor
    compactor = ConversationCompactor(
        client, threshold_tokens=config.compaction_threshold,
    )

    last_input_tokens = 0

    for round_num in range(MAX_TOOL_ROUNDS):
        # Compact conversation if it's getting large
        if round_num > 0 and last_input_tokens > 0:
            compacted = compactor.maybe_compact(
                conversation_history, last_input_tokens, audit,
            )
            if compacted:
                LOG.info("Conversation compacted to save tokens.")

        call_kwargs: Dict[str, Any] = dict(
            model=config.model,
            max_tokens=config.max_tokens,
            messages=conversation_history,
        )
        if sys_prompt:
            # Wrap as a cacheable text block so the large EDA system prompt
            # is served from cache on subsequent rounds (~90% discount).
            call_kwargs["system"] = [{
                "type": "text",
                "text": sys_prompt,
                "cache_control": {"type": "ephemeral"},
            }]
        if registry.has_tools():
            tool_params = registry.list_api_params()
            # Cache the tool definitions too — they're large and don't change
            # mid-session. Marker on the last tool caches the whole list.
            if tool_params:
                tool_params = [dict(t) for t in tool_params]
                tool_params[-1]["cache_control"] = {"type": "ephemeral"}
            call_kwargs["tools"] = tool_params

        LOG.debug("Round %d: sending request to %s", round_num, config.model)
        t0 = time.time()
        try:
            response = _call_api_with_retry(client, call_kwargs)
        except anthropic.AuthenticationError as exc:
            return "[Daisy: authentication failed — check your API key: %s]" % exc
        except anthropic.APIConnectionError as exc:
            return "[Daisy: connection failed after %d retries — %s]" % (
                MAX_API_RETRIES + 1, exc,
            )
        except anthropic.APIStatusError as exc:
            # Recover from oversized prompt via emergency compaction
            if exc.status_code == 400 and "prompt is too long" in str(exc):
                LOG.warning(
                    "Prompt too long, attempting emergency compaction..."
                )
                compacted = compactor.maybe_compact(
                    conversation_history, 999_999, audit,
                )
                if compacted:
                    continue  # retry with compacted history
            return "[Daisy: API error %d after retries — %s]" % (
                exc.status_code, exc,
            )
        elapsed = time.time() - t0

        last_input_tokens = response.usage.input_tokens

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

        # Budget check
        budget_status = audit.check_budget()
        if budget_status == "exceeded":
            cost = audit.get_session_cost()
            return (
                "[Daisy: session budget of $%.2f reached (current: $%.4f). "
                "Use --budget to increase or --budget 0 for unlimited.]"
                % (config.budget, cost)
            )
        if budget_status == "warning":
            cost = audit.get_session_cost()
            LOG.warning(
                "Session cost $%.4f approaching budget $%.2f",
                cost, config.budget,
            )

        # Context window warning
        if response.usage.input_tokens > CONTEXT_TOKEN_WARNING:
            LOG.warning(
                "Context is large (%d input tokens). "
                "Consider starting a new session to avoid degraded responses.",
                response.usage.input_tokens,
            )

        # Serialize assistant content blocks to plain dicts. Preserve thinking
        # blocks verbatim (signatures must round-trip unchanged) so extended
        # thinking works if it ever gets enabled.
        assistant_content: List[Dict[str, Any]] = []
        for block in response.content:
            btype = block.type
            if btype == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif btype == "thinking":
                assistant_content.append({
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": getattr(block, "signature", ""),
                })
            elif btype == "redacted_thinking":
                assistant_content.append({
                    "type": "redacted_thinking",
                    "data": block.data,
                })
            else:
                LOG.debug("Unhandled content block type: %s", btype)

        conversation_history.append({"role": "assistant", "content": assistant_content})

        stop_reason = response.stop_reason

        # pause_turn: server-side tool iteration cap hit. Re-send to resume.
        if stop_reason == "pause_turn":
            LOG.debug("Round %d: pause_turn, resuming", round_num)
            continue

        # If Claude is done (no tool calls), return the final text
        if stop_reason != "tool_use":
            if stop_reason == "refusal":
                LOG.warning("Claude refused to respond (safety stop)")
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
            LOG.info("[tool] %s", tool_name)

            # Check cache for read-only tools
            cache_key = _make_cache_key(tool_name, tool_input)
            if tool_name in _CACHEABLE_TOOLS and cache_key in tool_cache:
                result_str = tool_cache[cache_key]
                is_error = False
                audit.log_tool_cache_hit(tool_name, round_num)
                LOG.debug("Tool %s served from cache", tool_name)
            else:
                t_tool = time.time()
                try:
                    raw_result = registry.execute(tool_name, tool_input)
                    result_str = raw_result if isinstance(raw_result, str) else str(raw_result or "")
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

                # Spill oversized results to file (safety net)
                if not is_error and len(result_str) > MAX_TOOL_RESULT:
                    original_len = len(result_str)
                    spill_path = _spill_to_file(result_str, tool_name, config)
                    result_str = json.dumps({
                        "spilled_to_file": spill_path,
                        "original_size": original_len,
                        "preview": result_str[:2000],
                        "message": (
                            "Output too large for context (%d chars). "
                            "Full output saved to %s. Use run_command"
                            "('head/tail/grep ...') or read_file to examine."
                            % (original_len, spill_path)
                        ),
                    })
                    LOG.info(
                        "Tool %s output spilled to %s (%d chars)",
                        tool_name, spill_path, original_len,
                    )

                if is_error:
                    LOG.warning("Tool %s failed: %s", tool_name, result_str)
                else:
                    LOG.debug("Tool %s completed in %.3fs", tool_name, tool_elapsed)
                    if tool_name in _CACHEABLE_TOOLS:
                        # Cache read-only tool result
                        tool_cache[cache_key] = result_str
                    elif tool_cache:
                        # Mutating tool: invalidate cache — earlier read_file
                        # etc. results may now be stale.
                        LOG.debug(
                            "Clearing tool cache after mutating tool %s",
                            tool_name,
                        )
                        tool_cache.clear()

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
                "is_error": is_error,
            })

        conversation_history.append({"role": "user", "content": tool_results})

    return "[Daisy: max tool rounds (%d) reached, stopping]" % MAX_TOOL_ROUNDS
