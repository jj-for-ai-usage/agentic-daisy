"""Agentic Daisy -- CLI entry point."""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import stat
import sys
from typing import Any, Optional

from .agent_loop import run_agent_loop
from .audit import AuditLogger
from .built_in_tools import build_default_system_prompt, create_default_registry
from .config import (
    DaisyConfig, DEFAULT_MODEL, DEFAULT_MAX_TOKENS,
    DEFAULT_MEMORY_DIR, DEFAULT_LOG_DIR,
)
from .tool_registry import ToolRegistry
from . import render

try:
    import readline  # noqa: F401  -- enabling arrow-key history in input()
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

LOG = logging.getLogger("daisy")

_DAISY_DIR = os.path.expanduser("~/.daisy")
_REPL_HISTORY_FILE = os.path.join(_DAISY_DIR, "repl_history")
_USER_CONFIG_FILE = os.path.join(_DAISY_DIR, "config")
_API_KEY_FILE = os.path.join(_DAISY_DIR, "api_key")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Agentic Daisy -- Air-gapped Claude toolkit with tool use.",
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
                    help=(
                        "Model to use (default: %s). "
                        "Available: claude-haiku-4-5 (fastest, cheapest), "
                        "claude-sonnet-4-5 (balanced), "
                        "claude-opus-4 (most capable). "
                        "Also accepts dated versions like claude-sonnet-4-5-20250514"
                    ) % DEFAULT_MODEL)
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
    ap.add_argument("--no-budget-prompt", action="store_true",
                    help="In non-interactive mode, do NOT prompt on stdin to raise "
                         "the budget when it's exceeded (default: prompt enabled)")
    ap.add_argument("--no-stream", action="store_true",
                    help="Disable streaming output (wait for full reply, then print)")
    ap.add_argument("--no-rich", action="store_true",
                    help="Disable rich/markdown rendering even if rich is installed")
    ap.add_argument("--compaction-threshold", type=int, default=80_000,
                    help="Input-token threshold to trigger conversation compaction (default: 80000)")
    ap.add_argument("--temperature", type=float, default=0.3,
                    help="Sampling temperature 0.0-1.0 (default: 0.3, lower = more consistent)")
    ap.add_argument("--max-tool-rounds", type=int, default=20,
                    help="Max tool execution rounds per message (default: 20)")
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

    # Apply user defaults from ~/.daisy/config (only fields that weren't
    # explicitly given on the CLI). Today: model only. Keep this tiny.
    user_defaults = _load_user_config()
    if "model" in user_defaults and args.model == DEFAULT_MODEL:
        args.model = user_defaults["model"]

    if args.no_rich:
        render.set_force_plain(True)

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
        temperature=args.temperature,
        max_tool_rounds=args.max_tool_rounds,
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
        # First-run wizard if we're attached to a tty on both ends.
        if sys.stdin.isatty() and sys.stdout.isatty():
            _first_run_wizard()
            sys.exit(0)
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
            registry = create_default_registry(config)
            if not args.no_shell:
                from .tools import load_execution_tools
                load_execution_tools(
                    config, registry, audit,
                    interactive=args.interactive and not args.admin,
                    workspace_dir=config.workspace_dir,
                )

        # Set default system prompt if user didn't provide one
        if not config.system_prompt and registry.has_tools():
            config.system_prompt = build_default_system_prompt(registry, config)

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

        streaming = not args.no_stream
        stream_cb = render.render_stream_chunk if streaming else None

        if args.interactive:
            _interactive_loop(
                config, registry, audit, history=conversation_history,
                session_mgr=session_mgr, session_name=args.session,
                stream_callback=stream_cb,
            )
        elif args.message:
            pending_msg: Optional[str] = args.message
            while pending_msg is not None:
                result = run_agent_loop(
                    config, pending_msg, registry, audit, conversation_history,
                    stream_callback=stream_cb,
                )
                if (
                    _is_budget_exceeded_result(result)
                    and not args.no_budget_prompt
                    and _prompt_raise_budget_stdin(config, audit)
                ):
                    continue  # retry the same message with the new cap
                # When streaming, the assistant text was already printed live;
                # only print the trailing string if it's a [Daisy: ...] status
                # message (errors, budget, etc.) or if streaming was disabled.
                if not streaming:
                    render.render_assistant(result)
                elif result.startswith("[Daisy:"):
                    print(result)
                pending_msg = None
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


def _apply_budget_command(
    arg: str, config: DaisyConfig, audit: AuditLogger,
) -> str:
    """Handle a /budget argument string. Returns a status line for the user.

    Forms:
      ""           -> show current cost / cap
      "1.50"       -> set cap to $1.50
      "+0.50"      -> add $0.50 to current cap
      "0"          -> unlimited
    """
    cost = audit.get_session_cost()
    current = audit.get_budget()
    arg = arg.strip()
    if not arg:
        cap_str = "$%.2f" % current if current is not None else "unlimited"
        return "Cost: $%.4f / Budget: %s" % (cost, cap_str)
    try:
        if arg.startswith("+"):
            delta = float(arg[1:])
            if delta <= 0:
                return "Error: /budget +<delta> requires a positive number."
            base = current if current is not None else 0.0
            new_cap: Optional[float] = base + delta
        else:
            val = float(arg)
            if val < 0:
                return "Error: budget must be >= 0 (use 0 for unlimited)."
            new_cap = None if val == 0 else val
    except ValueError:
        return "Error: could not parse '%s' as a dollar amount." % arg
    audit.set_budget(new_cap)
    config.budget = new_cap
    cap_str = "$%.2f" % new_cap if new_cap is not None else "unlimited"
    return "Budget updated -> %s (current cost: $%.4f)" % (cap_str, cost)


def _prompt_raise_budget_stdin(
    config: DaisyConfig, audit: AuditLogger,
) -> bool:
    """Non-interactive stdin prompt after a budget-exceeded result.

    Returns True if the user raised the budget and the caller should retry.
    Returns False on EOF / empty / decline.
    """
    if not sys.stdin.isatty():
        # Best-effort: still try to read one line in case stdin is a pipe with
        # an answer queued. If EOF, we just give up.
        pass
    print(
        "Budget exceeded. Enter a new cap (e.g. 2.00, +0.50, 0 for unlimited) "
        "or blank to abort:",
        file=sys.stderr,
    )
    try:
        line = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False
    if not line:
        return False
    line = line.strip()
    if not line:
        return False
    msg = _apply_budget_command(line, config, audit)
    print(msg, file=sys.stderr)
    return msg.startswith("Budget updated")


def _is_budget_exceeded_result(result: str) -> bool:
    return result.startswith("[Daisy: session budget")


def _load_user_config() -> dict:
    """Read ~/.daisy/config (key=value lines). Returns empty dict on any error."""
    out: dict = {}
    if not os.path.exists(_USER_CONFIG_FILE):
        return out
    try:
        with open(_USER_CONFIG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError as exc:
        LOG.debug("user config load failed: %s", exc)
    return out


def _save_user_config(values: dict) -> None:
    os.makedirs(_DAISY_DIR, exist_ok=True)
    with open(_USER_CONFIG_FILE, "w") as f:
        for k, v in values.items():
            f.write("%s=%s\n" % (k, v))


def _first_run_wizard() -> None:
    """Interactive first-run setup. Writes ~/.daisy/api_key with mode 0600,
    creates the standard subdirectories, optionally records a default model.
    On any abort (Ctrl-C, EOF) prints a message and returns; the caller
    should exit afterward."""
    print("Welcome to Agentic Daisy. Let's set up your API key (one-time).")
    print()
    print("  Get a key at: https://console.anthropic.com/settings/keys")
    print()
    try:
        key = getpass.getpass("Paste your API key (sk-ant-...) [hidden]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return
    if not key:
        print("No key entered. Aborted.", file=sys.stderr)
        return
    if not key.startswith("sk-ant-"):
        print("Warning: key does not start with 'sk-ant-'. Saving anyway.",
              file=sys.stderr)

    try:
        os.makedirs(_DAISY_DIR, mode=0o700, exist_ok=True)
        # O_CREAT|O_TRUNC|O_WRONLY with mode 0600 -- mode is set at create
        # time so there's no chmod-after race.
        fd = os.open(
            _API_KEY_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
        # Belt-and-suspenders chmod in case umask interfered on an existing file.
        os.chmod(_API_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
        print("Saved key to %s (mode 0600)." % _API_KEY_FILE)
    except OSError as exc:
        print("ERROR: failed to write key file: %s" % exc, file=sys.stderr)
        return

    # Standard subdirs
    for sub in ("logs", "memory", "sessions", "workspace"):
        try:
            os.makedirs(os.path.join(_DAISY_DIR, sub), mode=0o700, exist_ok=True)
        except OSError as exc:
            print("Warning: could not create %s: %s" % (sub, exc), file=sys.stderr)

    print()
    print("Optional: pick a default model")
    print("  1) claude-haiku-4-5    (fast, cheap)         [default]")
    print("  2) claude-sonnet-4-5   (balanced)")
    print("  3) claude-opus-4       (most capable)")
    try:
        choice = input("Choice [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"
    model_map = {
        "1": "claude-haiku-4-5",
        "2": "claude-sonnet-4-5",
        "3": "claude-opus-4",
    }
    model = model_map.get(choice, "claude-haiku-4-5")
    try:
        _save_user_config({"model": model})
        print("Default model set: %s (saved to %s)" % (model, _USER_CONFIG_FILE))
    except OSError as exc:
        print("Warning: could not save user config: %s" % exc, file=sys.stderr)

    print()
    print("Setup complete. Re-run your command.")


def _load_readline_history() -> None:
    if not _HAS_READLINE:
        return
    try:
        os.makedirs(_DAISY_DIR, exist_ok=True)
        if os.path.exists(_REPL_HISTORY_FILE):
            readline.read_history_file(_REPL_HISTORY_FILE)
        readline.set_history_length(1000)
    except OSError as exc:
        LOG.debug("readline history load failed: %s", exc)


def _save_readline_history() -> None:
    if not _HAS_READLINE:
        return
    try:
        readline.write_history_file(_REPL_HISTORY_FILE)
    except OSError as exc:
        LOG.debug("readline history save failed: %s", exc)


def _interactive_loop(
    config: DaisyConfig,
    registry: ToolRegistry,
    audit: AuditLogger,
    history: list,
    session_mgr: Optional[Any] = None,
    session_name: Optional[str] = None,
    stream_callback: Optional[Any] = None,
) -> None:
    streaming = stream_callback is not None
    print("Agentic Daisy (interactive). Type 'exit' or Ctrl-D to quit.")
    print("  Model: %s | Budget: %s | Tools: %d loaded%s" % (
        config.model,
        "$%.2f" % config.budget if config.budget else "unlimited",
        len(registry.list_names()),
        " | streaming" if streaming else "",
    ))
    print()
    _load_readline_history()
    try:
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
            if user_input.startswith("/budget"):
                arg = user_input[len("/budget"):].strip()
                print(_apply_budget_command(arg, config, audit))
                print()
                continue
            try:
                result = run_agent_loop(
                    config, user_input, registry, audit, history,
                    stream_callback=stream_callback,
                )
            except KeyboardInterrupt:
                print("\n[Daisy] interrupted -- history preserved\n", file=sys.stderr)
                continue
            # When streaming, the assistant text was already printed live;
            # only show the trailing string if it's a [Daisy:] status message.
            if streaming:
                if result.startswith("[Daisy:"):
                    print("%s\n" % result)
                else:
                    print()  # spacer
            else:
                render.render_assistant(result)
                print()
            if _is_budget_exceeded_result(result):
                print(
                    'Tip: type "/budget <amount>" or "/budget +<delta>" to raise '
                    'the cap, then re-send your message.\n',
                    file=sys.stderr,
                )
            # Auto-save session after each turn
            if session_mgr and session_name:
                try:
                    session_mgr.save(session_name, history)
                except OSError as exc:
                    LOG.warning("Failed to save session: %s", exc)
    finally:
        _save_readline_history()


if __name__ == "__main__":
    main()
