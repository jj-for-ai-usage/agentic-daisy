"""Agentic Daisy -- Core agentic conversation loop."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import anthropic

from .audit import AuditLogger, BudgetExceededError
from .config import DaisyConfig
from .tool_registry import ToolRegistry, _ToolTimeoutError, TOOL_TIMEOUT

LOG = logging.getLogger("daisy")

MAX_API_RETRIES = 4
RETRY_BASE_DELAY = 2  # seconds; doubles each retry: 2, 4, 8, 16

# Rough per-message token budget to warn before overflow.
# Leave headroom for the response; warn if input approaches this.
CONTEXT_TOKEN_WARNING = 150_000

# Max chars for a tool result before spilling to file.
# ~12,500 tokens -- keeps individual results safe even with many rounds.
MAX_TOOL_RESULT = 50_000

# Tools whose results can be cached within a single agent loop (read-only tools)
_CACHEABLE_TOOLS = frozenset({
    "read_file", "search_files", "find_files", "list_directory",
    "directory_tree", "search_memory", "list_memories", "get_env",
    "load_skill", "list_tasks", "get_task",
    "get_batch_results",
    # Mempalace-port reads
    "list_wings", "list_rooms", "get_taxonomy", "get_drawer",
    "traverse", "find_tunnels", "recall",
    # Knowledge-graph reads
    "kg_query", "kg_timeline", "kg_stats",
})


def _do_api_call(client, call_kwargs: Dict[str, Any], stream_callback) -> Any:
    """One API call. If stream_callback is provided, stream text deltas to it
    and return the final assembled Message; otherwise do a plain create().

    Uses the ``beta.messages`` namespace because the ``context_management``
    kwarg (server-side compaction) is only available there in anthropic 0.88.
    """
    if stream_callback is None:
        return client.beta.messages.create(**call_kwargs)
    with client.beta.messages.stream(**call_kwargs) as stream:
        for text in stream.text_stream:
            stream_callback(text)
        return stream.get_final_message()


def _call_api_with_retry(
    client, call_kwargs: Dict[str, Any], stream_callback=None,
) -> Any:
    """Call messages.create (or .stream) with exponential backoff on transient errors."""
    last_exc = None
    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return _do_api_call(client, call_kwargs, stream_callback)
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
                "Connection error (attempt %d/%d): %s -- retrying in %ds...",
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
                raise  # 400, 401, 403 etc. -- not transient
    raise last_exc  # type: ignore[misc]


def _spill_to_file(content: str, tool_name: str, config) -> str:
    """Save oversized tool output to a workspace file, return the path.

    Filename uses seconds + microseconds + pid so two spills within the same
    wall-clock second (back-to-back tools) don't collide.
    """
    from datetime import datetime
    workspace = getattr(config, "workspace_dir", "/tmp")
    os.makedirs(workspace, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', tool_name)
    filename = "tool_%s_%s_%d.txt" % (safe_name, ts, os.getpid())
    path = os.path.join(workspace, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def _rollback_to_snapshot(history: List[Dict[str, Any]], snapshot_len: int) -> None:
    """Truncate *history* back to its pre-invocation length.

    When run_agent_loop bails out mid-turn (auth error, connection error,
    budget exceeded, ...), everything it appended since the snapshot must be
    discarded in one shot: the initial user(text), any assistant(tool_use)
    blocks, and any user(tool_results) from completed rounds. A naive
    "pop last user" is wrong after a tool-use round because popping
    user(tool_results) would orphan the preceding assistant(tool_use),
    while leaving it would give two consecutive user messages on the next
    turn. Truncating to the snapshot length handles both cases.
    """
    if snapshot_len < 0 or snapshot_len > len(history):
        return
    del history[snapshot_len:]


def _heal_orphaned_tool_uses(history: List[Dict[str, Any]]) -> int:
    """Walk *history* and repair any assistant(tool_use) blocks that lack a
    matching user(tool_result) in the following message.

    A healed orphan gets an injected synthetic tool_result with
    is_error=True so the conversation pairing is valid on the next API
    call. Returns the number of orphans healed.

    Safe to call on clean history (no-op). O(n) over the full conversation.
    Defensive recovery path for interactive sessions whose history was
    corrupted by earlier bugs, interrupted runs, or beta stop reasons
    (``compaction`` / ``pause_turn``) that the loop previously mishandled.
    """
    healed = 0
    i = 0
    while i < len(history):
        msg = history[i]
        if msg.get("role") != "assistant":
            i += 1
            continue
        content = msg.get("content") or []
        if not isinstance(content, list):
            i += 1
            continue
        tool_use_ids = [
            b["id"] for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use" and "id" in b
        ]
        if not tool_use_ids:
            i += 1
            continue

        next_msg = history[i + 1] if i + 1 < len(history) else None
        next_content = (
            next_msg.get("content")
            if (next_msg and next_msg.get("role") == "user")
            else None
        )

        if isinstance(next_content, list):
            done_ids = {
                b["tool_use_id"] for b in next_content
                if isinstance(b, dict) and b.get("type") == "tool_result"
                and "tool_use_id" in b
            }
            missing = [tid for tid in tool_use_ids if tid not in done_ids]
            if missing:
                for tid in missing:
                    next_content.append({
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "Healed: orphaned tool_use recovered on resume.",
                        "is_error": True,
                    })
                healed += len(missing)
            i += 2
            continue

        # No matching user message at all after this assistant(tool_use):
        # insert a synthetic one.
        synthetic = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": "Healed: orphaned tool_use recovered on resume.",
                    "is_error": True,
                }
                for tid in tool_use_ids
            ],
        }
        history.insert(i + 1, synthetic)
        healed += len(tool_use_ids)
        i += 2
    return healed


def _make_cache_key(tool_name: str, tool_input: dict) -> str:
    """Deterministic cache key for a tool call."""
    return tool_name + ":" + json.dumps(tool_input, sort_keys=True)


def _cleanup_old_spill_files(workspace: str, max_age_days: int = 7) -> None:
    """Remove spill files older than *max_age_days*."""
    if not os.path.isdir(workspace):
        return
    cutoff = time.time() - (max_age_days * 86400)
    for fname in os.listdir(workspace):
        if not fname.startswith("tool_"):
            continue
        fpath = os.path.join(workspace, fname)
        try:
            if os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                LOG.debug("Cleaned up old spill file: %s", fname)
        except OSError:
            pass


def run_agent_loop(
    config: DaisyConfig,
    user_message: str,
    registry: ToolRegistry,
    audit: AuditLogger,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    stream_callback: Optional[Any] = None,
) -> str:
    """
    Run a full agentic conversation starting from *user_message*.

    Returns the final text response from Claude.
    Mutates *conversation_history* in place so the caller can reuse it
    for multi-turn interactive sessions.
    """
    # Clean up old spill files on session start
    try:
        _cleanup_old_spill_files(getattr(config, "workspace_dir", ""))
    except OSError as exc:
        LOG.warning("Spill cleanup failed: %s -- continuing", exc)

    client = anthropic.Anthropic(api_key=config.api_key)

    if conversation_history is None:
        conversation_history = []

    # Defensive: heal any orphaned tool_use blocks inherited from a prior
    # turn (interrupted run, past framework bug, or a compaction/pause_turn
    # stop reason that wasn't handled in an older version of this loop).
    # O(n) no-op on clean history.
    orphans_healed = _heal_orphaned_tool_uses(conversation_history)
    if orphans_healed > 0:
        LOG.warning(
            "Healed %d orphaned tool_use block(s) from prior conversation",
            orphans_healed,
        )

    # Snapshot before appending anything so bail-out paths can restore the
    # history to a valid pre-invocation state in one operation.
    history_snapshot_len = len(conversation_history)
    conversation_history.append({"role": "user", "content": user_message})

    # Build kwargs for the API call
    sys_prompt = system_prompt or config.system_prompt or ""

    # Per-turn tool result cache (read-only tools only)
    tool_cache: Dict[str, str] = {}

    for round_num in range(config.max_tool_rounds):
        if round_num == 0:
            print("[Daisy] thinking...", file=sys.stderr, flush=True)

        call_kwargs: Dict[str, Any] = dict(
            model=config.model,
            max_tokens=config.max_tokens,
            messages=conversation_history,
            temperature=config.temperature,
        )
        if sys_prompt:
            call_kwargs["system"] = [
                {
                    "type": "text",
                    "text": sys_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if registry.has_tools():
            call_kwargs["tools"] = registry.list_api_params(cache_last=True)

        # Server-side context management: Anthropic automatically clears
        # old tool_use/tool_result blocks when the conversation crosses
        # config.compaction_threshold input tokens, keeping the most recent
        # tool uses to preserve working context. Replaces daisy's
        # hand-rolled ConversationCompactor. Only available on the beta
        # messages namespace (which _do_api_call uses) with the
        # 'context-management-2025-06-27' beta header.
        #
        # Edit type history: 'compact_20260112' is in the anthropic 0.88
        # SDK types but the server's context-management beta only accepts
        # 'clear_thinking_20251015' and 'clear_tool_uses_20250919'.
        # clear_tool_uses is the right choice for Daisy's tool-heavy
        # EDA workload -- log excerpts and command outputs accumulate
        # fast, and clearing old tool blocks is the biggest token win.
        call_kwargs["context_management"] = {
            "edits": [
                {
                    "type": "clear_tool_uses_20250919",
                    "trigger": {
                        "type": "input_tokens",
                        "value": config.compaction_threshold,
                    },
                    "keep": {"type": "tool_uses", "value": 3},
                },
            ],
        }
        call_kwargs["betas"] = ["context-management-2025-06-27"]

        LOG.debug("Round %d: sending request to %s", round_num, config.model)
        t0 = time.time()
        try:
            response = _call_api_with_retry(client, call_kwargs, stream_callback)
        except anthropic.AuthenticationError as exc:
            _rollback_to_snapshot(conversation_history, history_snapshot_len)
            return "[Daisy: authentication failed -- check your API key: %s]" % exc
        except anthropic.APIConnectionError as exc:
            _rollback_to_snapshot(conversation_history, history_snapshot_len)
            return "[Daisy: connection failed after %d retries -- %s]" % (
                MAX_API_RETRIES + 1, exc,
            )
        except anthropic.APIStatusError as exc:
            # Server-side compaction handles oversized prompts upstream, so
            # we no longer have a client-side emergency compaction path.
            _rollback_to_snapshot(conversation_history, history_snapshot_len)
            return "[Daisy: API error %d after retries -- %s]" % (
                exc.status_code, exc,
            )
        elapsed = time.time() - t0

        # Newline after any streamed text so the next stderr status line
        # ([Daisy] running ..., [Daisy] thinking ...) doesn't collide.
        if stream_callback is not None:
            stream_callback("\n")

        # Track cache tokens if available
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0

        audit.log_api_call(
            model=config.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            latency_s=elapsed,
            round_num=round_num,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
        )
        LOG.debug(
            "Round %d: %d input / %d output tokens (cache: %d write, %d read), stop=%s (%.2fs)",
            round_num,
            response.usage.input_tokens,
            response.usage.output_tokens,
            cache_write,
            cache_read,
            response.stop_reason,
            elapsed,
        )

        # Budget check
        budget_status = audit.check_budget()
        if budget_status == "exceeded":
            cost = audit.get_session_cost()
            _rollback_to_snapshot(conversation_history, history_snapshot_len)
            return (
                "[Daisy: session budget of $%.2f reached (current: $%.4f). "
                "Use --budget to increase or --budget 0 for unlimited.]"
                % (config.budget, cost)
            )
        if budget_status == "warning":
            cost = audit.get_session_cost()
            print(
                "[Daisy] approaching budget: $%.4f / $%.2f"
                % (cost, config.budget),
                file=sys.stderr,
            )
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

        # Decide what to do next based on the ACTUAL content, not stop_reason.
        # Trusting stop_reason alone is unsafe: the beta endpoint emits eight
        # stop reasons (end_turn, max_tokens, stop_sequence, tool_use,
        # pause_turn, compaction, refusal, model_context_window_exceeded) and
        # several of them can carry tool_use blocks that MUST be executed (or
        # synthesized) to keep the history pairing valid. In particular,
        # "compaction" fires whenever the server-side context_management edit
        # (clear_tool_uses_20250919) triggers on long sessions, and its
        # response body may include a partially-generated tool_use.
        has_tool_use = any(
            b.get("type") == "tool_use" for b in assistant_content
        )

        if not has_tool_use:
            # "compaction" and "pause_turn" mean the server paused mid-turn
            # and wants us to send the conversation back to continue. No
            # tool calls to execute; just loop to the next round so the
            # server can resume from where it stopped.
            if response.stop_reason in ("compaction", "pause_turn"):
                LOG.debug(
                    "Round %d: stop_reason=%s with no tool_use; continuing",
                    round_num, response.stop_reason,
                )
                continue
            # Terminal stop reason: return the accumulated text.
            parts = [b["text"] for b in assistant_content if b.get("type") == "text"]
            return "\n".join(parts) if parts else ""

        # Execute each tool call and collect results.
        # CRITICAL: the assistant(tool_use) was just appended above. We MUST
        # append a matching user(tool_results) before any control-flow exit
        # (KeyboardInterrupt, unexpected exception) or the conversation
        # history is left in a state that guarantees an API 400 next round.
        appended_tool_use_ids = [
            b["id"] for b in assistant_content
            if b.get("type") == "tool_use"
        ]
        tool_results: List[Dict[str, Any]] = []
        interrupted = False
        try:
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                print("[Daisy] running %s..." % tool_name, file=sys.stderr, flush=True)
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
                        available = ", ".join(registry.list_names())
                        result_str = "Error: unknown tool '%s'. Available: %s" % (tool_name, available)
                        is_error = True
                    except _ToolTimeoutError:
                        result_str = (
                            "Tool '%s' timed out after %ds. "
                            "Try a smaller input or break the work into steps."
                            % (tool_name, TOOL_TIMEOUT)
                        )
                        is_error = True
                    except Exception as exc:
                        err_msg = str(exc)
                        if len(err_msg) > 500:
                            err_msg = err_msg[:500] + "...[truncated]"
                        result_str = "Error executing %s: %s: %s" % (
                            tool_name, type(exc).__name__, err_msg,
                        )
                        is_error = True

                    tool_elapsed = time.time() - t_tool
                    audit.log_tool_execution(
                        tool_name=tool_name,
                        success=not is_error,
                        latency_s=tool_elapsed,
                        round_num=round_num,
                    )

                    # Spill oversized results to file (safety net).
                    # CRITICAL: this must never raise -- if it did, the assistant
                    # tool_use block above would be left in history without a
                    # matching tool_result, causing a guaranteed API 400 next turn.
                    if not is_error and len(result_str) > MAX_TOOL_RESULT:
                        original_len = len(result_str)
                        try:
                            spill_path = _spill_to_file(result_str, tool_name, config)
                            result_str = json.dumps({
                                "spilled_to_file": spill_path,
                                "original_size": original_len,
                                "preview": result_str[:2000],
                                "message": (
                                    "Output too large for context (%d chars). "
                                    "Full output saved to %s. "
                                    "Use run_command('head/tail/grep ...') or "
                                    "read_file to examine specific parts."
                                    % (original_len, spill_path)
                                ),
                            })
                            LOG.info(
                                "Tool %s output spilled to %s (%d chars)",
                                tool_name, spill_path, original_len,
                            )
                        except (OSError, ValueError, TypeError) as exc:
                            LOG.warning(
                                "Spill-to-file failed for %s: %s -- truncating in memory",
                                tool_name, exc,
                            )
                            result_str = (
                                result_str[:MAX_TOOL_RESULT]
                                + "\n[...truncated %d chars; spill failed: %s]"
                                % (original_len - MAX_TOOL_RESULT, exc)
                            )

                    if is_error:
                        LOG.warning("Tool %s failed: %s", tool_name, result_str)
                    else:
                        LOG.debug("Tool %s completed in %.3fs", tool_name, tool_elapsed)
                        # Cache result for read-only tools
                        if tool_name in _CACHEABLE_TOOLS:
                            tool_cache[cache_key] = result_str

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                    "is_error": is_error,
                })
        except KeyboardInterrupt:
            interrupted = True
            LOG.warning("KeyboardInterrupt during tool execution -- preserving history pairing")
        finally:
            # Synthesize a tool_result for any tool_use that didn't get one
            # (e.g. KeyboardInterrupt mid-loop). This keeps the API contract.
            done_ids = {r["tool_use_id"] for r in tool_results}
            for tid in appended_tool_use_ids:
                if tid not in done_ids:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "Interrupted before execution.",
                        "is_error": True,
                    })
            conversation_history.append({"role": "user", "content": tool_results})

        if interrupted:
            raise KeyboardInterrupt

    return (
        "[Daisy: reached %d tool rounds. Break the task into smaller steps, "
        "or increase with --max-tool-rounds.]" % config.max_tool_rounds
    )
