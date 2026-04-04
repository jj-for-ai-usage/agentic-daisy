"""Agentic Daisy — CLI entry point."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Optional

from .agent_loop import run_agent_loop
from .audit import AuditLogger
from .built_in_tools import build_default_system_prompt, create_default_registry
from .config import DaisyConfig, DEFAULT_MODEL, DEFAULT_MAX_TOKENS
from .tool_registry import ToolRegistry

LOG = logging.getLogger("daisy")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Agentic Daisy — Air-gapped Claude toolkit with tool use.",
    )
    ap.add_argument(
        "message",
        nargs="?",
        default=None,
        help="Message to send (single-shot mode)",
    )
    ap.add_argument("--api-key", default=None,
                    help="Anthropic API key (overrides ANTHROPIC_API_KEY env var)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="Model to use (default: %s)" % DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="Max response tokens (default: %d)" % DEFAULT_MAX_TOKENS)
    ap.add_argument("--system", default=None,
                    help="System prompt")
    ap.add_argument("--memory-dir", default=None,
                    help="Memory storage directory (default: ~/.daisy/memory)")
    ap.add_argument("--log-dir", default=None,
                    help="Audit log directory (default: ~/.daisy/logs)")
    ap.add_argument("--interactive", action="store_true",
                    help="Interactive REPL mode")
    ap.add_argument("--no-tools", action="store_true",
                    help="Disable tool use (plain chat)")
    ap.add_argument("--no-shell", action="store_true",
                    help="Disable shell command tool")
    ap.add_argument("--session", default=None,
                    help="Session name for conversation persistence")
    ap.add_argument("--list-sessions", action="store_true",
                    help="List saved sessions and exit")
    ap.add_argument("--debug", action="store_true",
                    help="Enable DEBUG logging")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = DaisyConfig(
        api_key=args.api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        memory_dir=args.memory_dir or DaisyConfig().memory_dir,
        log_dir=args.log_dir or DaisyConfig().log_dir,
        system_prompt=args.system,
        debug=args.debug,
    )

    # Handle --list-sessions early (no API key needed)
    if args.list_sessions:
        from .session import SessionManager
        sm = SessionManager()
        sessions = sm.list_sessions()
        if not sessions:
            print("No saved sessions.")
        else:
            for s in sessions:
                print("  %-20s  %d turns  (updated: %s)" % (
                    s["name"], s["turns"], s["updated"],
                ))
        sys.exit(0)

    if not config.api_key:
        LOG.error(
            "No API key provided. "
            "Pass --api-key or set env var:  "
            "export ANTHROPIC_API_KEY='sk-ant-...'  (bash)  |  "
            "setenv ANTHROPIC_API_KEY 'sk-ant-...'  (csh/tcsh)"
        )
        sys.exit(1)

    audit = None
    try:
        audit = AuditLogger(config.log_dir, config.model)

        if args.no_tools:
            registry = ToolRegistry()
        else:
            registry = create_default_registry(config)
            # Register shell tool (needs audit + interactive context)
            if not args.no_shell:
                from .shell_tool import make_run_command, make_run_python
                registry.register(
                    name="run_command",
                    description=(
                        "Execute a shell command on the server and return "
                        "stdout, stderr, and exit code."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": (
                                    "Shell command to execute (via /bin/sh -c)"
                                ),
                            },
                            "timeout": {
                                "type": "integer",
                                "description": (
                                    "Timeout in seconds (default: 30, max: 300)"
                                ),
                            },
                        },
                        "required": ["command"],
                    },
                    handler=make_run_command(
                        audit, interactive=args.interactive,
                    ),
                )
                registry.register(
                    name="run_python",
                    description=(
                        "Execute a Python script and return stdout, stderr, "
                        "and exit code. Use for multi-line data processing, "
                        "log analysis, config parsing, or any complex logic."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": (
                                    "Timeout in seconds (default: 60, max: 300)"
                                ),
                            },
                        },
                        "required": ["code"],
                    },
                    handler=make_run_python(
                        audit, interactive=args.interactive,
                    ),
                )

        # Set default system prompt if user didn't provide one
        if not config.system_prompt and registry.has_tools():
            config.system_prompt = build_default_system_prompt(registry)

        # Session persistence
        session_mgr = None
        conversation_history = []  # type: list
        if args.session:
            from .session import SessionManager
            session_mgr = SessionManager()
            loaded = session_mgr.load(args.session)
            if loaded:
                conversation_history = loaded
                print("Resumed session '%s' (%d messages)" % (
                    args.session, len(loaded),
                ))

        if args.interactive:
            _interactive_loop(
                config, registry, audit, history=conversation_history,
                session_mgr=session_mgr, session_name=args.session,
            )
        elif args.message:
            result = run_agent_loop(
                config, args.message, registry, audit, conversation_history,
            )
            print(result)
            if session_mgr and args.session:
                session_mgr.save(args.session, conversation_history)
        else:
            print(
                "ERROR: Provide a message or use --interactive.",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        if audit is not None:
            print("\n[%s]" % audit.get_session_summary(), file=sys.stderr)
            audit.log_session_end()


def _interactive_loop(
    config: DaisyConfig,
    registry: ToolRegistry,
    audit: AuditLogger,
    history: list,
    session_mgr: Optional[Any] = None,
    session_name: Optional[str] = None,
) -> None:
    print("Agentic Daisy (interactive). Type 'exit' or Ctrl-D to quit.\n")
    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye.")
            break
        if not user_input:
            continue
        result = run_agent_loop(config, user_input, registry, audit, history)
        print("\n%s\n" % result)
        # Auto-save session after each turn
        if session_mgr and session_name:
            session_mgr.save(session_name, history)


if __name__ == "__main__":
    main()
