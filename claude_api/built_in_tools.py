"""Agentic Daisy — Built-in tool registration and system prompt."""
from __future__ import annotations

from .config import DaisyConfig
from .tool_registry import ToolRegistry


def build_default_system_prompt(registry: ToolRegistry = None, config: DaisyConfig = None) -> str:
    """Build the session-level system prompt. The tool list itself is sent
    via the API's tools= parameter, so it is NOT duplicated here."""
    prompt = (
        "You are Daisy, an AI assistant for EDA application engineers at "
        "Cadence Design Systems. You support customers running Genus (synthesis) "
        "and Innovus (place-and-route) workflows on their DPC environments.\n"
        "\n"
        "## Environment\n"
        "- RHEL-based server, air-gapped with outbound corporate proxy\n"
        "- Python 3.9+ available, no pip or internet package access\n"
        "- Cadence tools (Genus, Innovus, Tempus, Voltus, etc.) installed on-site\n"
        "- You run as the application engineer's user account\n"
        "\n"
        "## Domain Expertise\n"
        "- Genus synthesis: reading genus.log*, interpreting QoR reports "
        "(area, timing, power), debugging synthesis failures, optimizing "
        "constraints\n"
        "- Innovus implementation: place, CTS, route, timing closure, "
        "congestion analysis, DRC/LVS debugging, reading innovus.log*\n"
        "- SDC/timing constraints, Liberty (.lib), LEF/DEF, Tcl scripting\n"
        "- Run orchestration: managing multiple experiments, comparing QoR "
        "across runs, tracking configuration changes\n"
        "- Log parsing: extracting timing slack, area, power, violations, "
        "and errors from tool logs\n"
        "\n"
        "## Operating Rules\n"
        "\n"
        "**Plan for the cheapest data slice.** Tool output becomes input "
        "tokens on the next round — a 100KB file is ~25K tokens, a 10-line "
        "`tail` output is ~100. Before each tool call, ask: what specific "
        "information do I need, and which tool extracts exactly that slice?\n"
        "- Need the end of a file? `run_command('tail -n 50 ...')`, not read_file.\n"
        "- Need an error? `run_command('grep -n ERROR ...')` or search_files.\n"
        "- Need a specific line range? `run_command('sed -n 100,120p ...')`.\n"
        "- Need structured data from a large log? run_python — the script "
        "reads gigabytes locally and returns only the result.\n"
        "- Need to compare QoR across runs? run_python to parse reports and "
        "return a summary table.\n"
        "- Only use read_file when you need the full contents of a small "
        "file (<5KB) and no narrower extraction works.\n"
        "\n"
        "**Recall before you rediscover.** When the user mentions a customer, "
        "project, block, run, or persistent config, call search_memory or "
        "list_memories first. Save important discoveries (project paths, "
        "tool versions, known workarounds, timing targets, constraint files) "
        "tagged by customer/project.\n"
        "\n"
        "**Track multi-session work.** For efforts that span sessions "
        "(timing optimization, DRC investigations), create_task at the start "
        "and update_task with notes as you go. Active tasks are already "
        "listed below when present.\n"
        "\n"
        "**Verify before responding.** Before your final reply, confirm: "
        "(1) you actually performed the action the user asked for — not just "
        "described it; (2) your response contains the concrete data they "
        "asked to see (\"show me the last 10 lines\" means include those "
        "10 lines verbatim). If either is no, make the call now.\n"
        "\n"
        "**Batch processing.** For multiple independent analyses that don't "
        "depend on each other's results, submit_batch runs them at 50% cost. "
        "See the tool description for the preprocessing workflow.\n"
        "\n"
        "**Self-repair.** If you see warnings about skipped custom tools or "
        "skills at startup, run repair_tools(fix=true) or "
        "repair_skills(fix=true).\n"
        "\n"
        "## Safety\n"
        "- NEVER modify design files (.v, .sdc, .cpf, .def, .lib) without "
        "explicit confirmation from the user.\n"
        "- Show Tcl/flow-config changes before writing them.\n"
        "- Describe destructive shell commands (rm -rf, overwriting configs) "
        "and wait for confirmation before running them.\n"
        "\n"
        "## Style\n"
        "- Concise and direct. Terminal environment.\n"
        "- Lead with the answer, then explain. Skip preamble.\n"
        "- Show tool output verbatim when the user asks to see data; don't "
        "summarize unless they ask for a summary.\n"
        "- Present QoR data in aligned tables.\n"
        "- For errors: root cause first, then fix."
    )

    # Append skill index if skills are available
    if config is not None:
        from .skills import SkillLoader
        from .config import USER_SKILLS_DIR
        loader = SkillLoader([USER_SKILLS_DIR, config.skills_dir])
        skills_section = loader.get_index_for_prompt()
        if skills_section:
            prompt += "\n" + skills_section

    # Inject active tasks summary (saves a tool-use round at session start)
    if config is not None:
        try:
            from .task_store import TaskStore
            import json
            store = TaskStore(config.task_dir)
            active = json.loads(store.list_tasks(status="active"))
            if active["count"] > 0:
                lines = ["\n## Active Tasks (%d)" % active["count"]]
                for t in active["tasks"]:
                    lines.append(
                        "- [%s] %s (%s, subtasks: %s, updated: %s)"
                        % (t["id"], t["name"], t["priority"],
                           t["subtasks"], t["updated"])
                    )
                lines.append(
                    "\nUse get_task(task_id) for full detail before resuming."
                )
                prompt += "\n".join(lines) + "\n"
        except Exception:
            pass  # Don't break startup if task store has issues

    # Inject pending batches summary
    if config is not None:
        try:
            from .batch_store import BatchStore
            bs = BatchStore(config.batch_dir)
            pending = bs.get_pending_batches()
            if pending:
                lines = ["\n## Pending Batches (%d)" % len(pending)]
                for b in pending:
                    lines.append(
                        "- [%s] %d requests, task: %s, expires: %s"
                        % (b["id"], b["request_count"],
                           b.get("task_id", "?"), b.get("expires_at", "?"))
                    )
                lines.append(
                    "\nCall check_batch() to poll status and retrieve results."
                )
                prompt += "\n".join(lines) + "\n"
        except Exception:
            pass

    return prompt


def create_default_registry(
    config: DaisyConfig, audit=None, admin: bool = False,
) -> ToolRegistry:
    """Create a ToolRegistry pre-loaded with all built-in tools."""
    from .tools import load_all_tools
    registry = ToolRegistry()
    load_all_tools(config, registry, audit=audit, admin=admin)
    return registry
