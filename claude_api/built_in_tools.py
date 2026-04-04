"""Agentic Daisy — Built-in tool registration and system prompt."""
from __future__ import annotations

from .config import DaisyConfig
from .file_tools import (
    append_file, directory_tree, edit_file, find_files, get_env,
    list_directory, read_file, search_files, write_file,
)
from .memory import MemoryStore
from .tool_registry import ToolRegistry


def build_default_system_prompt(registry: ToolRegistry) -> str:
    """Build a system prompt describing the environment and available tools."""
    tools = registry.list_tool_summaries()
    tool_lines = "\n".join(
        "- %s: %s" % (t["name"], t["description"]) for t in tools
    )
    return (
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
        "## Your Tools\n"
        "%s\n"
        "\n"
        "## Domain Expertise\n"
        "You are an expert in:\n"
        "- Genus synthesis: reading genus.log*, interpreting QoR reports (area, "
        "timing, power), debugging synthesis failures, optimizing constraints\n"
        "- Innovus implementation: place, CTS, route, timing closure, "
        "congestion analysis, DRC/LVS debugging, reading innovus.log*\n"
        "- SDC/timing constraints, Liberty (.lib), LEF/DEF, Tcl scripting\n"
        "- Run orchestration: managing multiple experiments, comparing QoR "
        "across runs, tracking configuration changes\n"
        "- Log parsing: extracting timing slack, area, power, violations, "
        "and errors from tool logs\n"
        "\n"
        "## How to Work\n"
        "\n"
        "### Reasoning Protocol (follow this for EVERY request)\n"
        "Before making any tool call, think through these steps:\n"
        "\n"
        "**Step 1 — Understand:** What is the user actually asking for? "
        "Restate the goal in your head. 'Show me the last 10 lines' means "
        "they want 10 lines, not the whole file. 'Debug this timing failure' "
        "means find the root cause, not dump every report.\n"
        "\n"
        "**Step 2 — Recall:** Do I already know something relevant? Check "
        "memory (list_memories/search_memory) for prior context about this "
        "project, customer, or issue. Don't re-discover what you already "
        "saved.\n"
        "\n"
        "**Step 3 — Plan the cheapest path:** What is the minimum number "
        "of tool calls and the minimum amount of data needed to answer "
        "this? Map out the approach before acting:\n"
        "  - Start narrow: use targeted commands (tail, grep, head) first\n"
        "  - Zoom in only if needed: don't front-load all the data\n"
        "  - For complex tasks, decompose into steps. 'Debug timing' = "
        "(1) grep for violations, (2) check the worst path, (3) look at "
        "constraints — not 'read every file and figure it out'\n"
        "\n"
        "**Step 4 — Execute:** Make the tool calls. Use the smallest tool "
        "for the job. If run_python can extract exactly what you need from "
        "a large file, use it instead of reading the file into context.\n"
        "\n"
        "**Step 5 — Verify and stop:** Did you answer the question? If "
        "yes, stop. Don't make extra tool calls 'to be thorough' or 'for "
        "completeness.' Every unnecessary call costs money and time. If "
        "the answer is clear after one tool call, respond immediately.\n"
        "\n"
        "### Memory (critical)\n"
        "- At the START of every conversation, call list_memories or "
        "search_memory to recall prior context about the customer, project, "
        "known issues, and tool configurations.\n"
        "- Save important discoveries: customer project names, directory "
        "structures, tool versions, known workarounds, timing targets, "
        "constraint file paths.\n"
        "- Tag memories by customer/project for easy retrieval.\n"
        "\n"
        "### CORE PRINCIPLE: Minimize Token Cost\n"
        "Every token you consume costs real money. The user is paying per "
        "token. Before EVERY tool call, think: what is the smallest amount "
        "of data I need to answer this question, and which tool gets me "
        "exactly that — nothing more?\n"
        "\n"
        "**The cost equation:**\n"
        "- Each character of tool output becomes ~0.25 input tokens on the "
        "next API call.\n"
        "- A 100KB file = ~25,000 tokens = ~$0.02-0.08 per round trip.\n"
        "- A 10-line tail/grep output = ~100 tokens = ~$0.0001.\n"
        "- That's a 100-200x cost difference for the same answer.\n"
        "\n"
        "**The decision framework (use this for EVERY tool call):**\n"
        "1. What specific information do I need? (not 'the file' — the "
        "exact lines, pattern, metric, or answer)\n"
        "2. What is the smallest slice of data that contains it?\n"
        "3. Which tool extracts exactly that slice?\n"
        "\n"
        "**Apply this thinking:**\n"
        "- Need the end of a file? run_command('tail ...'), not read_file.\n"
        "- Need to find an error? run_command('grep -n ERROR ...') or "
        "search_files, not read_file.\n"
        "- Need a specific section? run_command('sed -n 100,120p ...'), "
        "not read_file.\n"
        "- Need to extract structured data from a large log? run_python "
        "with a script that reads and filters locally — only the result "
        "comes back to you, not the whole file.\n"
        "- Need to compare QoR across runs? run_python to parse reports "
        "and build a summary table locally.\n"
        "- Need to search across many files? search_files returns "
        "structured matches — cheaper than grepping and reading results.\n"
        "- ONLY use read_file when you genuinely need the full contents "
        "of a small file (< 5KB) or there is no narrower extraction "
        "possible.\n"
        "\n"
        "**Think of run_python as your power tool:** when the question "
        "requires complex logic, write a script that does all the heavy "
        "lifting locally. The script can read gigabytes of logs, parse "
        "them, and return just the 10 lines you need. This keeps your "
        "context window small and your costs low.\n"
        "\n"
        "### Safety\n"
        "- NEVER modify design files (.v, .sdc, .cpf, .def, .lib) without "
        "explicit confirmation.\n"
        "- When editing Tcl scripts or flow configs, always show the change "
        "before writing.\n"
        "- Shell commands: describe what you're running and why. Avoid "
        "destructive commands (rm -rf, overwriting configs) unless asked.\n"
        "- run_command timeout: 30s default, 300s max. For long-running "
        "commands, increase the timeout parameter.\n"
        "- run_python timeout: 60s default, 300s max.\n"
        "- Output is truncated at 50K characters for both.\n"
        "- Scripts from run_python are saved to .daisy/workspace/ for user "
        "inspection. The script_path is returned in the result.\n"
        "- When writing files with write_file, prefer .daisy/workspace/ "
        "for temporary scripts rather than /tmp/.\n"
        "\n"
        "### Style\n"
        "- Be concise and direct. This is a terminal environment.\n"
        "- Lead with the answer, then explain. Skip preamble.\n"
        "- When presenting QoR data, use aligned tables.\n"
        "- For errors, identify root cause first, then suggest fix." % tool_lines
    )


def create_default_registry(config: DaisyConfig) -> ToolRegistry:
    """Create a ToolRegistry pre-loaded with all built-in tools."""
    registry = ToolRegistry()

    # -- Memory tools --
    mem = MemoryStore(config.memory_dir)

    registry.register(
        name="save_memory",
        description=(
            "Save a piece of information for later retrieval. "
            "Use this to remember important context, decisions, or facts "
            "across conversations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Short identifier for this memory "
                        "(e.g. 'project-status', 'user-preference-timezone')"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "The content to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional categorization tags",
                },
            },
            "required": ["key", "value"],
        },
        handler=mem.save_memory,
    )

    registry.register(
        name="search_memory",
        description=(
            "Search saved memories by keyword (matches against keys and values) "
            "or by tag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (substring match on key and value)",
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by exact tag match",
                },
            },
        },
        handler=mem.search_memory,
    )

    registry.register(
        name="delete_memory",
        description="Delete a saved memory by its key.",
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key of the memory to delete",
                },
            },
            "required": ["key"],
        },
        handler=mem.delete_memory,
    )

    registry.register(
        name="list_memories",
        description=(
            "List all saved memory keys with their tags and "
            "last-updated timestamps."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=mem.list_memories,
    )

    # -- File tools --

    registry.register(
        name="read_file",
        description="Read the contents of a file. Returns up to 100K characters.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to read",
                },
            },
            "required": ["path"],
        },
        handler=read_file,
    )

    registry.register(
        name="write_file",
        description=(
            "Write content to a file. Creates parent directories if needed. "
            "Overwrites existing content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=write_file,
    )

    registry.register(
        name="edit_file",
        description=(
            "Surgical find-and-replace in a file. Only changes the matched "
            "text — much cheaper than rewriting the whole file with write_file. "
            "The old_string must match exactly once."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=edit_file,
    )

    registry.register(
        name="append_file",
        description=(
            "Append content to the end of a file without reading it first. "
            "Creates the file if it doesn't exist."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to append to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to append",
                },
            },
            "required": ["path", "content"],
        },
        handler=append_file,
    )

    registry.register(
        name="get_env",
        description=(
            "Get a system environment snapshot: hostname, user, cwd, "
            "Python version, OS, disk usage. One call instead of multiple "
            "shell commands."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=get_env,
    )

    registry.register(
        name="list_directory",
        description="List directory contents with file types and sizes.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory path to list (default: current directory)"
                    ),
                },
            },
        },
        handler=list_directory,
    )

    registry.register(
        name="search_files",
        description=(
            "Search file contents by regex pattern. Returns matching lines "
            "with file paths and line numbers. Walks directories recursively."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory to search in (default: current directory)"
                    ),
                },
                "include": {
                    "type": "string",
                    "description": (
                        "Glob pattern to filter filenames (e.g. '*.py', '*.conf')"
                    ),
                },
                "context_lines": {
                    "type": "integer",
                    "description": (
                        "Number of lines of context around each match (default: 0)"
                    ),
                },
            },
            "required": ["pattern"],
        },
        handler=search_files,
    )

    registry.register(
        name="find_files",
        description=(
            "Find files by name using a glob pattern. "
            "Recursively walks directories."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match filenames (e.g. '*.py', '*.log')"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory to search in (default: current directory)"
                    ),
                },
            },
            "required": ["pattern"],
        },
        handler=find_files,
    )

    registry.register(
        name="directory_tree",
        description=(
            "Show a recursive directory tree with depth limit. "
            "Good for understanding project structure."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Root directory for the tree (default: current directory)"
                    ),
                },
                "max_depth": {
                    "type": "integer",
                    "description": (
                        "Maximum depth to recurse (default: 3, max: 5)"
                    ),
                },
            },
        },
        handler=directory_tree,
    )

    return registry
