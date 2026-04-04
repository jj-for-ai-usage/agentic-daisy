#!/usr/bin/env python3
"""Agentic Daisy — Full System Test Suite.

Usage:
    python3 -m claude_api.test_suite              # offline tests only
    python3 -m claude_api.test_suite --api-key sk-...  # offline + online
    python3 -m claude_api.test_suite --quick      # fast subset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
from typing import List, Tuple

# ── Test infrastructure ────────────────────────────────────

PASSED = 0
FAILED = 0
SKIPPED = 0
RESULTS = []  # type: List[Tuple[str, str, str]]  # (name, status, detail)


def test(name: str):
    """Decorator that registers and runs a test function."""
    def decorator(fn):
        fn._test_name = name
        return fn
    return decorator


def run_test(fn, skip_reason: str = ""):
    global PASSED, FAILED, SKIPPED
    name = getattr(fn, "_test_name", fn.__name__)
    if skip_reason:
        SKIPPED += 1
        RESULTS.append((name, "SKIP", skip_reason))
        print("  [ SKIP ] %s — %s" % (name, skip_reason))
        return
    try:
        fn()
        PASSED += 1
        RESULTS.append((name, "PASS", ""))
        print("  [ PASS ] %s" % name)
    except AssertionError as exc:
        FAILED += 1
        RESULTS.append((name, "FAIL", str(exc)))
        print("  [ FAIL ] %s — %s" % (name, exc))
    except Exception as exc:
        FAILED += 1
        detail = "%s: %s" % (type(exc).__name__, exc)
        RESULTS.append((name, "FAIL", detail))
        print("  [ FAIL ] %s — %s" % (name, detail))


# ── Offline Tests (no API key needed) ─────────────────────

@test("Import all modules")
def test_imports():
    from claude_api.config import DaisyConfig
    from claude_api.audit import AuditLogger
    from claude_api.tool_registry import ToolRegistry
    from claude_api.memory import MemoryStore
    from claude_api.built_in_tools import create_default_registry, build_default_system_prompt
    from claude_api.agent_loop import run_agent_loop, _call_api_with_retry
    from claude_api.file_tools import (
        read_file, write_file, list_directory,
        search_files, find_files, directory_tree,
    )
    from claude_api.shell_tool import make_run_command, make_run_python, _safe_env
    from claude_api.session import SessionManager
    from claude_api.cli import parse_args
    import anthropic
    assert anthropic.__version__, "Anthropic SDK version not found"


@test("Config defaults")
def test_config():
    from claude_api.config import DaisyConfig
    c = DaisyConfig()
    assert c.model == "claude-haiku-4-5"
    assert c.max_tokens == 4096
    assert "/.daisy/memory" in c.memory_dir
    assert "/.daisy/logs" in c.log_dir


@test("Tool registry: register + execute")
def test_registry():
    from claude_api.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.register("echo", "Echo input", {"type": "object"}, lambda msg="": msg)
    assert reg.has_tools()
    assert reg.execute("echo", {"msg": "hello"}) == "hello"
    params = reg.list_api_params()
    assert len(params) == 1
    assert params[0]["name"] == "echo"


@test("Tool registry: filters extra kwargs")
def test_registry_kwargs_filter():
    from claude_api.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.register("greet", "Greet", {"type": "object"}, lambda name="": "hi %s" % name)
    result = reg.execute("greet", {"name": "Alice", "mood": "happy", "extra": 123})
    assert result == "hi Alice", "Got: %s" % result


@test("Tool registry: unknown tool raises KeyError")
def test_registry_unknown():
    from claude_api.tool_registry import ToolRegistry
    reg = ToolRegistry()
    try:
        reg.execute("nonexistent", {})
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


@test("Memory: save + search + list + delete")
def test_memory():
    from claude_api.memory import MemoryStore
    d = tempfile.mkdtemp()
    try:
        mem = MemoryStore(d)
        # Save
        r = json.loads(mem.save_memory("server-os", "RHEL 9", ["infra"]))
        assert r["status"] == "created"
        # Update
        r = json.loads(mem.save_memory("server-os", "RHEL 9.3", ["infra"]))
        assert r["status"] == "updated"
        # Search by keyword
        r = json.loads(mem.search_memory(query="RHEL"))
        assert r["matches"] == 1
        assert "9.3" in r["results"][0]["value"]
        # Search by tag
        r = json.loads(mem.search_memory(tag="infra"))
        assert r["matches"] == 1
        # List
        r = json.loads(mem.list_memories())
        assert r["total"] == 1
        # Delete
        r = json.loads(mem.delete_memory("server-os"))
        assert r["status"] == "deleted"
        r = json.loads(mem.list_memories())
        assert r["total"] == 0
        # Delete non-existent
        r = json.loads(mem.delete_memory("nope"))
        assert r["status"] == "not_found"
    finally:
        shutil.rmtree(d)


@test("Memory: corrupted JSON recovery")
def test_memory_corruption():
    from claude_api.memory import MemoryStore
    d = tempfile.mkdtemp()
    try:
        mem_file = os.path.join(d, "memories.json")
        with open(mem_file, "w") as f:
            f.write('{"broken')  # corrupted
        # Suppress expected warning from corruption recovery
        logging.getLogger("daisy").setLevel(logging.CRITICAL)
        mem = MemoryStore(d)  # should NOT crash
        logging.getLogger("daisy").setLevel(logging.WARNING)
        assert len(mem._memories) == 0, "Should recover to empty"
    finally:
        shutil.rmtree(d)


@test("Memory: atomic save (file valid after write)")
def test_memory_atomic():
    from claude_api.memory import MemoryStore
    d = tempfile.mkdtemp()
    try:
        mem = MemoryStore(d)
        mem.save_memory("key1", "value1")
        # Verify file is valid JSON
        with open(os.path.join(d, "memories.json")) as f:
            data = json.load(f)
        assert len(data) == 1
    finally:
        shutil.rmtree(d)


@test("Memory: directory permissions are 0o700")
def test_memory_permissions():
    from claude_api.memory import MemoryStore
    d = tempfile.mkdtemp()
    try:
        mem_dir = os.path.join(d, "secure_mem")
        mem = MemoryStore(mem_dir)
        mode = os.stat(mem_dir).st_mode & 0o777
        assert mode == 0o700, "Expected 0700, got %o" % mode
    finally:
        shutil.rmtree(d)


@test("Audit: session lifecycle + cost tracking")
def test_audit():
    from claude_api.audit import AuditLogger
    d = tempfile.mkdtemp()
    try:
        audit = AuditLogger(d, "claude-haiku-4-5")
        audit.log_api_call("claude-haiku-4-5", 1000, 500, "end_turn", 1.5, 0)
        audit.log_tool_execution("save_memory", True, 0.01, 0)
        audit.log_shell_command("ls", 0, False, 0.1)
        # Cost check (haiku: 1000*0.80/1M + 500*4.00/1M = 0.0008 + 0.002 = 0.0028)
        cost = audit.get_session_cost()
        assert abs(cost - 0.0028) < 0.0001, "Cost wrong: %f" % cost
        summary = audit.get_session_summary()
        assert "1000 in" in summary
        assert "500 out" in summary
        audit.log_session_end()
        # Verify JSONL file exists and is parseable
        log_file = audit.log_file
        assert os.path.exists(log_file)
        with open(log_file) as f:
            lines = f.readlines()
        assert len(lines) == 5, "Expected 5 events, got %d" % len(lines)
        for line in lines:
            json.loads(line)  # each line must be valid JSON
    finally:
        shutil.rmtree(d)


@test("Audit: directory permissions are 0o700")
def test_audit_permissions():
    from claude_api.audit import AuditLogger
    d = tempfile.mkdtemp()
    try:
        log_dir = os.path.join(d, "secure_logs")
        audit = AuditLogger(log_dir, "test")
        mode = os.stat(log_dir).st_mode & 0o777
        assert mode == 0o700, "Expected 0700, got %o" % mode
    finally:
        shutil.rmtree(d)


@test("File tools: read + write + list")
def test_file_tools():
    from claude_api.file_tools import read_file, write_file, list_directory
    d = tempfile.mkdtemp()
    try:
        # Write
        r = json.loads(write_file(os.path.join(d, "test.txt"), "hello world"))
        assert r["status"] == "written"
        assert r["bytes"] == 11
        # Read
        r = json.loads(read_file(os.path.join(d, "test.txt")))
        assert r["content"] == "hello world"
        assert r["truncated"] is False
        # List
        r = json.loads(list_directory(d))
        assert r["count"] == 1
        assert r["entries"][0]["name"] == "test.txt"
    finally:
        shutil.rmtree(d)


@test("File tools: read non-existent file")
def test_file_tools_error():
    from claude_api.file_tools import read_file
    r = json.loads(read_file("/tmp/daisy_nonexistent_file_12345"))
    assert "error" in r


@test("File tools: search_files (regex)")
def test_search_files():
    from claude_api.file_tools import search_files
    r = json.loads(search_files(
        "def run_agent_loop",
        os.path.join(os.path.dirname(__file__)),
    ))
    assert r["matches"] >= 1, "Should find run_agent_loop"
    assert r["results"][0]["line_number"] > 0


@test("File tools: search_files with include filter")
def test_search_files_filter():
    from claude_api.file_tools import search_files
    r = json.loads(search_files(
        "import json",
        os.path.join(os.path.dirname(__file__)),
        include="*.py",
    ))
    assert r["matches"] >= 1


@test("File tools: search_files with context lines")
def test_search_files_context():
    from claude_api.file_tools import search_files
    r = json.loads(search_files(
        "MAX_TOOL_ROUNDS",
        os.path.join(os.path.dirname(__file__)),
        context_lines=2,
    ))
    assert r["matches"] >= 1
    assert "context" in r["results"][0]
    assert len(r["results"][0]["context"]) >= 1


@test("File tools: search_files invalid regex")
def test_search_files_bad_regex():
    from claude_api.file_tools import search_files
    r = json.loads(search_files("[invalid", "/tmp"))
    assert "error" in r


@test("File tools: find_files (glob)")
def test_find_files():
    from claude_api.file_tools import find_files
    r = json.loads(find_files("*.py", os.path.dirname(__file__)))
    assert r["count"] >= 10, "Should find at least 10 .py files, got %d" % r["count"]


@test("File tools: directory_tree")
def test_directory_tree():
    from claude_api.file_tools import directory_tree
    project_root = os.path.dirname(os.path.dirname(__file__))
    r = json.loads(directory_tree(project_root, max_depth=2))
    names = [e["name"] for e in r["tree"]]
    assert "bin" in names, "Should find bin/ in tree"
    assert "claude_api" in names, "Should find claude_api/ in tree"


@test("Shell tool: run_command")
def test_run_command():
    from claude_api.audit import AuditLogger
    from claude_api.shell_tool import make_run_command
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_command(audit, interactive=False)
    r = json.loads(handler(command="echo hello"))
    assert r["exit_code"] == 0
    assert "hello" in r["stdout"]


@test("Shell tool: run_command timeout")
def test_run_command_timeout():
    from claude_api.audit import AuditLogger
    from claude_api.shell_tool import make_run_command
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_command(audit, interactive=False)
    r = json.loads(handler(command="sleep 10", timeout=2))
    assert r["timed_out"] is True
    assert r["exit_code"] == -1


@test("Shell tool: run_python")
def test_run_python():
    from claude_api.audit import AuditLogger
    from claude_api.shell_tool import make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit, interactive=False)
    r = json.loads(handler(code="print(2 + 2)"))
    assert r["exit_code"] == 0
    assert "4" in r["stdout"]


@test("Shell tool: run_python error handling")
def test_run_python_error():
    from claude_api.audit import AuditLogger
    from claude_api.shell_tool import make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit, interactive=False)
    r = json.loads(handler(code="raise ValueError('boom')"))
    assert r["exit_code"] != 0
    assert "ValueError" in r["stderr"]


@test("Shell tool: run_python timeout")
def test_run_python_timeout():
    from claude_api.audit import AuditLogger
    from claude_api.shell_tool import make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit, interactive=False)
    r = json.loads(handler(code="import time; time.sleep(10)", timeout=2))
    assert r["timed_out"] is True


@test("Shell tool: API key NOT in subprocess env")
def test_api_key_scrubbed():
    from claude_api.shell_tool import _safe_env
    # Save and restore the real key so we don't clobber it for online tests
    original_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-secret-key"
        env = _safe_env()
        assert "ANTHROPIC_API_KEY" not in env, "API key leaked to subprocess!"
        assert "PATH" in env, "PATH should be preserved"
        # Also verify via actual subprocess
        from claude_api.audit import AuditLogger
        from claude_api.shell_tool import make_run_python
        audit = AuditLogger(tempfile.mkdtemp(), "test")
        handler = make_run_python(audit, interactive=False)
        r = json.loads(handler(code=(
            "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'NOT_FOUND'))"
        )))
        assert "NOT_FOUND" in r["stdout"], "API key visible in subprocess!"
    finally:
        # Restore the real key so online tests can use it
        if original_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = original_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)


@test("Session: save + load + list")
def test_session():
    from claude_api.session import SessionManager
    d = tempfile.mkdtemp()
    try:
        sm = SessionManager(d)
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        sm.save("test-session", history)
        # List
        sessions = sm.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "test-session"
        assert sessions[0]["turns"] == 2
        # Load
        loaded = sm.load("test-session")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        # Load non-existent
        assert sm.load("nope") is None
    finally:
        shutil.rmtree(d)


@test("Session: corrupted file recovery")
def test_session_corruption():
    from claude_api.session import SessionManager
    d = tempfile.mkdtemp()
    try:
        sm = SessionManager(d)
        with open(os.path.join(d, "broken.json"), "w") as f:
            f.write("{corrupt")
        logging.getLogger("daisy").setLevel(logging.CRITICAL)
        loaded = sm.load("broken")
        logging.getLogger("daisy").setLevel(logging.WARNING)
        assert loaded is None, "Should return None for corrupted session"
        sessions = sm.list_sessions()
        assert sessions[0]["updated"] == "corrupted"
    finally:
        shutil.rmtree(d)


@test("Session: directory permissions are 0o700")
def test_session_permissions():
    from claude_api.session import SessionManager
    d = tempfile.mkdtemp()
    try:
        sess_dir = os.path.join(d, "secure_sessions")
        sm = SessionManager(sess_dir)
        mode = os.stat(sess_dir).st_mode & 0o777
        assert mode == 0o700, "Expected 0700, got %o" % mode
    finally:
        shutil.rmtree(d)


@test("Session: name sanitization (path traversal prevention)")
def test_session_sanitize():
    from claude_api.session import SessionManager
    d = tempfile.mkdtemp()
    try:
        sm = SessionManager(d)
        # Attempt path traversal
        path = sm._session_path("../../etc/passwd")
        assert d in path, "Path should stay within session dir"
        assert ".." not in path, ".. should be stripped"
    finally:
        shutil.rmtree(d)


@test("Built-in tools: all 13 registered")
def test_builtin_tools():
    from claude_api.built_in_tools import create_default_registry
    from claude_api.config import DaisyConfig
    config = DaisyConfig(memory_dir=tempfile.mkdtemp())
    reg = create_default_registry(config)
    tools = reg.list_tool_summaries()
    names = {t["name"] for t in tools}
    expected = {
        "save_memory", "search_memory", "delete_memory", "list_memories",
        "read_file", "write_file", "edit_file", "append_file",
        "list_directory", "search_files", "find_files", "directory_tree",
        "get_env",
    }
    assert names == expected, "Missing: %s  Extra: %s" % (expected - names, names - expected)


@test("System prompt: generated with all tools")
def test_system_prompt():
    from claude_api.built_in_tools import create_default_registry, build_default_system_prompt
    from claude_api.config import DaisyConfig
    config = DaisyConfig(memory_dir=tempfile.mkdtemp())
    reg = create_default_registry(config)
    prompt = build_default_system_prompt(reg)
    assert "Daisy" in prompt
    assert "Genus" in prompt
    assert "Innovus" in prompt
    assert "save_memory" in prompt
    assert "search_files" in prompt
    assert "directory_tree" in prompt


@test("CLI: --help exits 0")
def test_cli_help():
    import subprocess
    project_root = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env["DAISY_PYTHON"] = sys.executable  # use current Python for testing
    result = subprocess.run(
        [os.path.join(project_root, "bin", "daisy"), "--help"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, "daisy --help failed: %s" % result.stderr
    assert "--interactive" in result.stdout
    assert "--session" in result.stdout
    assert "--no-shell" in result.stdout
    assert "--list-sessions" in result.stdout


@test("CLI: --list-sessions exits 0")
def test_cli_list_sessions():
    import subprocess
    project_root = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env["DAISY_PYTHON"] = sys.executable
    result = subprocess.run(
        [os.path.join(project_root, "bin", "daisy"), "--list-sessions"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, "daisy --list-sessions failed: %s" % result.stderr


# ── Online Tests (API key required) ───────────────────────

@test("Online: basic API call (no tools)")
def test_online_basic():
    """Send a simple message and verify we get a response."""
    from claude_api.config import DaisyConfig
    from claude_api.audit import AuditLogger
    from claude_api.tool_registry import ToolRegistry
    from claude_api.agent_loop import run_agent_loop
    d = tempfile.mkdtemp()
    config = DaisyConfig(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    audit = AuditLogger(d, config.model)
    registry = ToolRegistry()  # no tools
    result = run_agent_loop(
        config, "Reply with exactly: DAISY_OK", registry, audit,
        system_prompt="Reply with exactly the text the user asks for, nothing else.",
    )
    assert "DAISY_OK" in result, "Expected DAISY_OK, got: %s" % result[:200]
    audit.log_session_end()
    shutil.rmtree(d)


@test("Online: tool use loop (memory save + list)")
def test_online_tool_use():
    """Verify Claude can call tools and the loop works end-to-end."""
    from claude_api.config import DaisyConfig
    from claude_api.audit import AuditLogger
    from claude_api.built_in_tools import create_default_registry
    from claude_api.agent_loop import run_agent_loop
    d = tempfile.mkdtemp()
    mem_dir = os.path.join(d, "mem")
    config = DaisyConfig(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        memory_dir=mem_dir,
    )
    audit = AuditLogger(os.path.join(d, "logs"), config.model)
    registry = create_default_registry(config)
    result = run_agent_loop(
        config,
        "Save a memory with key 'test-color' and value 'blue', "
        "then list all memories and tell me the total count.",
        registry, audit,
        system_prompt=(
            "You have memory tools. Use save_memory to save the requested "
            "memory, then use list_memories to check. Report the total count."
        ),
    )
    # Verify memory was actually saved
    from claude_api.memory import MemoryStore
    mem = MemoryStore(mem_dir)
    memories = json.loads(mem.list_memories())
    assert memories["total"] >= 1, "Memory not saved. Response: %s" % result[:300]
    audit.log_session_end()
    shutil.rmtree(d)


# ── Test runner ────────────────────────────────────────────

OFFLINE_TESTS = [
    test_imports,
    test_config,
    test_registry,
    test_registry_kwargs_filter,
    test_registry_unknown,
    test_memory,
    test_memory_corruption,
    test_memory_atomic,
    test_memory_permissions,
    test_audit,
    test_audit_permissions,
    test_file_tools,
    test_file_tools_error,
    test_search_files,
    test_search_files_filter,
    test_search_files_context,
    test_search_files_bad_regex,
    test_find_files,
    test_directory_tree,
    test_run_command,
    test_run_command_timeout,
    test_run_python,
    test_run_python_error,
    test_run_python_timeout,
    test_api_key_scrubbed,
    test_session,
    test_session_corruption,
    test_session_permissions,
    test_session_sanitize,
    test_builtin_tools,
    test_system_prompt,
    test_cli_help,
    test_cli_list_sessions,
]

QUICK_TESTS = [
    test_imports,
    test_config,
    test_registry,
    test_memory,
    test_audit,
    test_file_tools,
    test_run_command,
    test_run_python,
    test_api_key_scrubbed,
    test_session,
    test_builtin_tools,
    test_system_prompt,
    test_cli_help,
]

ONLINE_TESTS = [
    test_online_basic,
    test_online_tool_use,
]


def main():
    global PASSED, FAILED, SKIPPED

    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    tests = QUICK_TESTS if args.quick else OFFLINE_TESTS
    label = "Quick" if args.quick else "Full"

    # Offline tests
    print("\n--- Offline Tests (%d) ---\n" % len(tests))
    t0 = time.time()
    for fn in tests:
        run_test(fn)

    # Online tests
    if not args.quick:
        print("\n--- Online Tests (%d) ---\n" % len(ONLINE_TESTS))
        for fn in ONLINE_TESTS:
            run_test(fn, skip_reason="" if has_key else "no API key")

    elapsed = time.time() - t0

    # Summary
    total = PASSED + FAILED + SKIPPED
    print("\n" + "=" * 50)
    print("%s Test Results: %d passed, %d failed, %d skipped (%d total) in %.1fs" % (
        label, PASSED, FAILED, SKIPPED, total, elapsed,
    ))
    if FAILED:
        print("\nFailed tests:")
        for name, status, detail in RESULTS:
            if status == "FAIL":
                print("  - %s: %s" % (name, detail))
        print()
    print("=" * 50)

    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
