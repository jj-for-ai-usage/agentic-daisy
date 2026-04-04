"""Agentic Daisy — CLI entry point."""
from __future__ import annotations

import argparse
import logging
import sys

from .agent_loop import run_agent_loop
from .audit import AuditLogger
from .built_in_tools import create_default_registry
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

        conversation_history = []  # type: list

        if args.interactive:
            _interactive_loop(config, registry, audit, conversation_history)
        elif args.message:
            result = run_agent_loop(
                config, args.message, registry, audit, conversation_history,
            )
            print(result)
        else:
            print(
                "ERROR: Provide a message or use --interactive.",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        if audit is not None:
            audit.log_session_end()


def _interactive_loop(
    config: DaisyConfig,
    registry: ToolRegistry,
    audit: AuditLogger,
    history: list,
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


if __name__ == "__main__":
    main()
