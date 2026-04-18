"""Agentic Daisy — CLI entry point."""
from __future__ import annotations

import argparse
import atexit
import glob
import logging
import os
import readline
import sys
from typing import Any, List, Optional

from .agent_loop import run_agent_loop
from .audit import AuditLogger
from .built_in_tools import build_default_system_prompt, create_default_registry
from .config import (
    DaisyConfig, DEFAULT_MODEL, DEFAULT_MAX_TOKENS,
    DEFAULT_MEMORY_DIR, DEFAULT_LOG_DIR, DEFAULT_REPL_HISTORY,
)
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
    ap.add_argument("--budget", type=float, default=1.0,
                    help="Max session cost in USD (default: $1.00, 0 = unlimited)")
    ap.add_argument("--compaction-threshold", type=int, default=80_000,
                    help="Input-token threshold to trigger conversation compaction (default: 80000)")
    ap.add_argument("--debug", action="store_true",
                    help="Enable DEBUG logging")
    ap.add_argument("--admin", action="store_true", default=False,
                    help=argparse.SUPPRESS)
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Suppress noisy third-party loggers (httpx prints every HTTP request)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.budget < 0:
        print(
            "ERROR: --budget must be >= 0 (got %s). Use 0 for unlimited."
            % args.budget,
            file=sys.stderr,
        )
        sys.exit(1)
    budget = args.budget if args.budget > 0 else None  # 0 = unlimited
    config = DaisyConfig(
        api_key=args.api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        memory_dir=args.memory_dir or DEFAULT_MEMORY_DIR,
        log_dir=args.log_dir or DEFAULT_LOG_DIR,
        system_prompt=args.system,
        debug=args.debug,
        budget=budget,
        compaction_threshold=args.compaction_threshold,
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
        from .config import DEFAULT_API_KEY_FILE
        print(
            "ERROR: No API key found.\n"
            "\n"
            "Options (in priority order):\n"
            "  1. bin/daisy --api-key 'sk-ant-...'\n"
            "  2. export ANTHROPIC_API_KEY='sk-ant-...'  (bash)\n"
            "     setenv ANTHROPIC_API_KEY 'sk-ant-...'  (csh/tcsh)\n"
            "  3. Store in key file (recommended for persistent use):\n"
            "       echo 'sk-ant-...' > %s\n"
            "       chmod 600 %s\n" % (DEFAULT_API_KEY_FILE, DEFAULT_API_KEY_FILE),
            file=sys.stderr,
        )
        sys.exit(1)

    audit = None
    try:
        audit = AuditLogger(config.log_dir, config.model, budget=config.budget)

        if args.no_tools:
            registry = ToolRegistry()
        else:
            registry = create_default_registry(config, audit=audit, admin=args.admin)
            if not args.no_shell:
                from .tools import load_execution_tools
                load_execution_tools(
                    config, registry, audit,
                    interactive=args.interactive and not args.admin,
                    workspace_dir=config.workspace_dir,
                )

        # Set default system prompt if user didn't provide one
        if not config.system_prompt and registry.has_tools():
            config.system_prompt = build_default_system_prompt(config=config)

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


def _path_complete(text: str, state: int) -> Optional[str]:
    """readline completer: glob-based filesystem path completion.

    Supports absolute paths, paths relative to cwd, and `~`/`~user`
    expansion. Directory matches are returned with a trailing `/` so
    repeated Tabs can descend into them. All matches for a given line
    are computed on state==0 and cached on the function object.
    """
    if state == 0:
        expanded = os.path.expanduser(text) if text.startswith("~") else text
        try:
            raw = glob.glob(expanded + "*")
        except (OSError, ValueError):
            raw = []
        matches: List[str] = []
        for p in raw:
            display = p
            # Re-fold ~ back in so the completed line keeps the user's prefix.
            if text.startswith("~"):
                home = os.path.expanduser("~")
                if display == home:
                    display = "~"
                elif display.startswith(home + os.sep):
                    display = "~" + display[len(home):]
            if os.path.isdir(p) and not display.endswith(os.sep):
                display = display + "/"
            matches.append(display)
        matches.sort()
        _path_complete.cache = matches  # type: ignore[attr-defined]
    cache = getattr(_path_complete, "cache", [])
    if state < len(cache):
        return cache[state]
    return None


def _init_repl_history(path: str) -> None:
    """Enable readline history persistence and path-completion for the REPL.

    Importing `readline` (done at module top) is what gives `input()`
    arrow-key navigation and emacs line editing — this function adds
    cross-session history recall plus Tab-completion on filesystem paths
    so pasted partial paths behave like in a normal shell.
    Silently degrades to in-session-only history if the file system
    is read-only or otherwise uncooperative; path completion is wired
    up independently of history so a history-file failure doesn't
    disable Tab.
    """
    # --- Tab completion on filesystem paths ---
    try:
        # Shell-like word delimiters so the completer only sees the
        # current whitespace-delimited word. Notably: no slashes, dots,
        # or dashes — those are legitimate path characters.
        readline.set_completer_delims(" \t\n")
        readline.set_completer(_path_complete)
        # GNU readline uses `tab: complete`; libedit (macOS) uses a
        # different syntax, so try both and ignore failures.
        try:
            readline.parse_and_bind("tab: complete")
        except Exception:
            pass
        if "libedit" in getattr(readline, "__doc__", "") or "":
            try:
                readline.parse_and_bind("bind ^I rl_complete")
            except Exception:
                pass
    except Exception as exc:
        LOG.debug("REPL tab-completion disabled (%s)", exc)

    # --- History file ---
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        try:
            readline.read_history_file(path)
        except OSError:
            pass  # first run, or unreadable — start with empty history
        readline.set_history_length(1000)

        def _save_history() -> None:
            try:
                readline.write_history_file(path)
                # Prompts may contain customer paths / project names —
                # tighten perms to match the ~/.daisy/api_key convention.
                os.chmod(path, 0o600)
            except OSError as exc:
                LOG.debug("Could not persist REPL history to %s: %s", path, exc)

        atexit.register(_save_history)
    except OSError as exc:
        LOG.debug("REPL history disabled (%s)", exc)


def _handle_runtime_command(
    user_input: str, config: DaisyConfig, audit: AuditLogger,
) -> bool:
    """Handle in-REPL slash/dash commands. Returns True if handled."""
    parts = user_input.split()
    if not parts:
        return False
    cmd = parts[0]
    if cmd not in ("--budget", "/budget"):
        return False
    if len(parts) < 2:
        cur = audit.get_session_cost()
        budget = "unlimited" if config.budget is None else "$%.2f" % config.budget
        print("Current budget: %s   spent: $%.4f" % (budget, cur))
        print("Usage: --budget <USD>   (0 = unlimited)")
        return True
    try:
        new_budget = float(parts[1])
    except ValueError:
        print("ERROR: --budget expects a number (got %r)" % parts[1])
        return True
    if new_budget < 0:
        print("ERROR: --budget must be >= 0 (got %s). Use 0 for unlimited." % new_budget)
        return True
    new = new_budget if new_budget > 0 else None
    config.budget = new
    audit._budget = new
    audit._warned_budget = False
    cur = audit.get_session_cost()
    label = "unlimited" if new is None else "$%.2f" % new
    print("Budget updated to %s   (current spend: $%.4f)" % (label, cur))
    return True


def _interactive_loop(
    config: DaisyConfig,
    registry: ToolRegistry,
    audit: AuditLogger,
    history: list,
    session_mgr: Optional[Any] = None,
    session_name: Optional[str] = None,
) -> None:
    _init_repl_history(DEFAULT_REPL_HISTORY)
    print("Agentic Daisy (interactive). Type 'exit' or Ctrl-D to quit.")
    print("Runtime commands: --budget <USD>   (0 = unlimited)\n")
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
        if _handle_runtime_command(user_input, config, audit):
            continue
        result = run_agent_loop(config, user_input, registry, audit, history)
        print("\n%s\n" % result)
        # Auto-save session after each turn
        if session_mgr and session_name:
            session_mgr.save(session_name, history)


if __name__ == "__main__":
    main()
