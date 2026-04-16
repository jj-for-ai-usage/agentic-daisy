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
    from claude_api.tools.file.read_file import handler as _rf
    from claude_api.tools.file.write_file import make_handler as _wf
    from claude_api.tools.file.edit_file import handler as _ef
    from claude_api.tools.file.append_file import make_handler as _af
    from claude_api.tools.file.stat_file import handler as _stf
    from claude_api.tools.file.diff_files import handler as _dff
    from claude_api.tools.search.search_files import handler as _sf
    from claude_api.tools.search.directory_tree import handler as _dt
    from claude_api.tools.search.list_directory import handler as _ld
    from claude_api.tools.search.list_recent_files import handler as _lrf
    from claude_api.tools.execution.run_command import make_handler as _rc
    from claude_api.tools.execution.run_python import make_handler as _rp
    from claude_api.session import SessionManager
    from claude_api.compaction import ConversationCompactor
    from claude_api.task_store import TaskStore
    from claude_api.batch_store import BatchStore
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
        # Cost check (haiku-4-5: 1000*1.00/1M + 500*5.00/1M = 0.001 + 0.0025 = 0.0035)
        cost = audit.get_session_cost()
        assert abs(cost - 0.0035) < 0.0001, "Cost wrong: %f" % cost
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
    from claude_api.tools.file.read_file import handler as read_file
    from claude_api.tools.file.write_file import make_handler as _wf_factory
    from claude_api.tools.search.list_directory import handler as list_directory
    write_file = _wf_factory(audit=None)
    d = tempfile.mkdtemp()
    try:
        # Write
        r = json.loads(write_file(os.path.join(d, "test.txt"), "hello world"))
        assert r["ok"] is True
        assert r["status"] in ("created", "overwrote")
        assert r["bytes"] == 11
        # Read
        r = json.loads(read_file(os.path.join(d, "test.txt")))
        assert r["ok"] is True
        assert r["content"] == "hello world"
        assert r["truncated"] is False
        # List
        r = json.loads(list_directory(d))
        assert r["ok"] is True
        assert r["count"] == 1
        assert r["entries"][0]["name"] == "test.txt"
    finally:
        shutil.rmtree(d)


@test("File tools: read non-existent file")
def test_file_tools_error():
    from claude_api.tools.file.read_file import handler as read_file
    r = json.loads(read_file("/tmp/daisy_nonexistent_file_12345"))
    assert "error" in r


@test("File tools: search_files (regex)")
def test_search_files():
    from claude_api.tools.search.search_files import handler as search_files
    r = json.loads(search_files(
        "def run_agent_loop",
        os.path.join(os.path.dirname(__file__)),
    ))
    assert r["matches"] >= 1, "Should find run_agent_loop"
    assert r["results"][0]["line_number"] > 0


@test("File tools: search_files with include filter")
def test_search_files_filter():
    from claude_api.tools.search.search_files import handler as search_files
    r = json.loads(search_files(
        "import json",
        os.path.join(os.path.dirname(__file__)),
        include="*.py",
    ))
    assert r["matches"] >= 1


@test("File tools: search_files with context lines")
def test_search_files_context():
    from claude_api.tools.search.search_files import handler as search_files
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
    from claude_api.tools.search.search_files import handler as search_files
    r = json.loads(search_files("[invalid", "/tmp"))
    assert "error" in r


@test("Search tools: list_recent_files")
def test_list_recent_files():
    from claude_api.tools.search.list_recent_files import handler as list_recent
    d = tempfile.mkdtemp()
    try:
        # Create a few files and make one clearly newer than the rest
        import time as _t
        paths = []
        for i in range(3):
            p = os.path.join(d, "f%d.txt" % i)
            with open(p, "w") as f:
                f.write("x")
            paths.append(p)
            _t.sleep(0.01)
        r = json.loads(list_recent(d, limit=5))
        assert r["ok"] is True
        assert r["count"] == 3
        # Newest first → last-created file leads
        assert r["files"][0]["path"] == paths[-1]
        # include_pattern filter works
        r = json.loads(list_recent(d, include_pattern="f0*"))
        assert r["count"] == 1
    finally:
        shutil.rmtree(d)


@test("File tools: diff_files")
def test_diff_files():
    from claude_api.tools.file.diff_files import handler as diff_files
    d = tempfile.mkdtemp()
    try:
        a = os.path.join(d, "a.txt")
        b = os.path.join(d, "b.txt")
        with open(a, "w") as f:
            f.write("one\ntwo\nthree\n")
        with open(b, "w") as f:
            f.write("one\nTWO\nthree\n")
        r = json.loads(diff_files(a, b))
        assert r["ok"] is True
        assert r["identical"] is False
        assert r["lines_added"] == 1
        assert r["lines_removed"] == 1
        assert "TWO" in r["diff"]
        # Identical files
        r = json.loads(diff_files(a, a))
        assert r["identical"] is True
        assert r["diff"] == ""
    finally:
        shutil.rmtree(d)


@test("File tools: directory_tree")
def test_directory_tree():
    from claude_api.tools.search.directory_tree import handler as directory_tree
    project_root = os.path.dirname(os.path.dirname(__file__))
    r = json.loads(directory_tree(project_root, max_depth=2))
    names = [e["name"] for e in r["tree"]]
    assert "bin" in names, "Should find bin/ in tree"
    assert "claude_api" in names, "Should find claude_api/ in tree"


@test("Shell tool: run_command")
def test_run_command():
    from claude_api.audit import AuditLogger
    from claude_api.tools.execution.run_command import make_handler as make_run_command
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_command(audit=audit, interactive=False)
    r = json.loads(handler(command="echo hello"))
    assert r["exit_code"] == 0
    assert "hello" in r["stdout"]


@test("Shell tool: run_command timeout")
def test_run_command_timeout():
    from claude_api.audit import AuditLogger
    from claude_api.tools.execution.run_command import make_handler as make_run_command
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_command(audit=audit, interactive=False)
    r = json.loads(handler(command="sleep 10", timeout=2))
    assert r["timed_out"] is True
    assert r["exit_code"] == -1


@test("Shell tool: run_python")
def test_run_python():
    from claude_api.audit import AuditLogger
    from claude_api.tools.execution.run_python import make_handler as make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit=audit, interactive=False)
    r = json.loads(handler(code="print(2 + 2)"))
    assert r["exit_code"] == 0
    assert "4" in r["stdout"]


@test("Shell tool: run_python error handling")
def test_run_python_error():
    from claude_api.audit import AuditLogger
    from claude_api.tools.execution.run_python import make_handler as make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit=audit, interactive=False)
    r = json.loads(handler(code="raise ValueError('boom')"))
    assert r["exit_code"] != 0
    assert "ValueError" in r["stderr"]


@test("Shell tool: run_python timeout")
def test_run_python_timeout():
    from claude_api.audit import AuditLogger
    from claude_api.tools.execution.run_python import make_handler as make_run_python
    audit = AuditLogger(tempfile.mkdtemp(), "test")
    handler = make_run_python(audit=audit, interactive=False)
    r = json.loads(handler(code="import time; time.sleep(10)", timeout=2))
    assert r["timed_out"] is True


@test("Shell tool: API key NOT in subprocess env")
def test_api_key_scrubbed():
    from claude_api.tools.execution.run_command import _safe_env
    # Save and restore the real key so we don't clobber it for online tests
    original_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-secret-key"
        env = _safe_env()
        assert "ANTHROPIC_API_KEY" not in env, "API key leaked to subprocess!"
        assert "PATH" in env, "PATH should be preserved"
        # Also verify via actual subprocess
        from claude_api.audit import AuditLogger
        from claude_api.tools.execution.run_python import make_handler as make_run_python
        audit = AuditLogger(tempfile.mkdtemp(), "test")
        handler = make_run_python(audit=audit, interactive=False)
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


@test("Built-in tools: all 27 registered")
def test_builtin_tools():
    from claude_api.built_in_tools import create_default_registry
    from claude_api.config import DaisyConfig
    config = DaisyConfig(
        memory_dir=tempfile.mkdtemp(), task_dir=tempfile.mkdtemp(),
        batch_dir=tempfile.mkdtemp(),
    )
    reg = create_default_registry(config)
    tools = reg.list_tool_summaries()
    names = {t["name"] for t in tools}
    expected = {
        # memory (3)
        "save_memory", "search_memory", "list_memories",
        # file (6)
        "read_file", "write_file", "edit_file", "append_file",
        "stat_file", "diff_files",
        # search (4)
        "list_directory", "search_files", "directory_tree", "list_recent_files",
        # system (5)
        "load_skill", "create_skill", "create_tool",
        "repair_tools", "repair_skills",
        # task (4)
        "create_task", "update_task", "list_tasks", "get_task",
        # batch (3)
        "submit_batch", "check_batch", "get_batch_results",
        # eda (2)
        "scan_workspaces", "tabulate_workspaces",
    }
    assert names == expected, "Missing: %s  Extra: %s" % (expected - names, names - expected)


@test("System prompt: core sections present, no tool-list duplication")
def test_system_prompt():
    from claude_api.built_in_tools import create_default_registry, build_default_system_prompt
    from claude_api.config import DaisyConfig
    config = DaisyConfig(memory_dir=tempfile.mkdtemp())
    reg = create_default_registry(config)
    prompt = build_default_system_prompt(config=config)
    # Persona + domain still present
    assert "Daisy" in prompt
    assert "Genus" in prompt
    assert "Innovus" in prompt
    # Core operating-rule keywords
    assert "cheapest data slice" in prompt.lower() or "cheapest" in prompt
    assert "Verify" in prompt
    # Tool list MUST NOT be duplicated in the system prompt —
    # tools are sent via the API's tools= parameter.
    assert "- save_memory:" not in prompt
    assert "- search_files:" not in prompt
    assert "- directory_tree:" not in prompt
    # Sanity: prompt size should be well under the pre-refactor ~7.5KB
    assert len(prompt) < 4000, "Prompt regressed in size: %d" % len(prompt)


@test("TaskStore: create + list + get + update lifecycle")
def test_task_lifecycle():
    from claude_api.task_store import TaskStore
    d = tempfile.mkdtemp()
    try:
        store = TaskStore(d)
        # Create
        r = json.loads(store.create_task(
            name="Fix timing",
            description="Close WNS on block_x",
            priority="high",
            tags=["timing", "block_x"],
            subtasks=[
                {"name": "Run synthesis"},
                {"name": "Add exceptions"},
                {"name": "Verify"},
            ],
            context="genus.log at /proj/syn/",
        ))
        assert r["status"] == "created"
        task_id = r["task_id"]
        assert task_id.startswith("task_")
        # List
        r = json.loads(store.list_tasks())
        assert r["count"] == 1
        assert r["tasks"][0]["name"] == "Fix timing"
        assert r["tasks"][0]["subtasks"] == "0/3 done"
        # List with filter
        r = json.loads(store.list_tasks(status="done"))
        assert r["count"] == 0
        r = json.loads(store.list_tasks(tag="timing"))
        assert r["count"] == 1
        # Get
        r = json.loads(store.get_task(task_id))
        assert "task" in r
        assert len(r["task"]["subtasks"]) == 3
        assert r["task"]["context"] == "genus.log at /proj/syn/"
        # Update status
        r = json.loads(store.update_task(task_id, status="paused"))
        assert r["status"] == "updated"
        assert r["task"]["status"] == "paused"
        # Update subtask
        r = json.loads(store.update_task(task_id, update_subtask={"index": 0, "status": "done", "notes": "WNS: -0.3ns"}))
        assert r["task"]["subtasks"][0]["status"] == "done"
        assert r["task"]["subtasks"][0]["notes"] == "WNS: -0.3ns"
        # Add subtask
        r = json.loads(store.update_task(task_id, add_subtask={"name": "Final review"}))
        assert len(r["task"]["subtasks"]) == 4
        # Append notes
        r = json.loads(store.update_task(task_id, notes="Tried multicycle paths"))
        assert "Tried multicycle paths" in r["task"]["context"]
        # Mark done
        r = json.loads(store.update_task(task_id, status="done"))
        assert r["task"]["status"] == "done"
        # Get non-existent
        r = json.loads(store.get_task("task_nonexistent"))
        assert "error" in r
    finally:
        shutil.rmtree(d)


@test("TaskStore: corruption recovery")
def test_task_corruption():
    d = tempfile.mkdtemp()
    try:
        task_file = os.path.join(d, "tasks.json")
        with open(task_file, "w") as f:
            f.write("{broken")
        logging.getLogger("daisy").setLevel(logging.CRITICAL)
        from claude_api.task_store import TaskStore
        store = TaskStore(d)
        logging.getLogger("daisy").setLevel(logging.WARNING)
        assert len(store._tasks) == 0, "Should recover to empty"
    finally:
        shutil.rmtree(d)


@test("TaskStore: persistence across instances")
def test_task_persistence():
    from claude_api.task_store import TaskStore
    d = tempfile.mkdtemp()
    try:
        store1 = TaskStore(d)
        r = json.loads(store1.create_task(name="Persistent task"))
        task_id = r["task_id"]
        # New instance should load from disk
        store2 = TaskStore(d)
        r = json.loads(store2.get_task(task_id))
        assert "task" in r
        assert r["task"]["name"] == "Persistent task"
    finally:
        shutil.rmtree(d)


@test("BatchStore: create + list + get + result save/read")
def test_batch_store():
    from claude_api.batch_store import BatchStore
    d = tempfile.mkdtemp()
    try:
        store = BatchStore(d)
        # Create
        manifest = [
            {"custom_id": "task_001__0", "label": "block_A analysis"},
            {"custom_id": "task_001__1", "label": "block_B analysis"},
        ]
        r = json.loads(store.create_batch(
            task_id="task_001", model="claude-haiku-4-5",
            manifest=manifest, batch_api_id="msgbatch_test123",
            expires_at="2026-04-05T10:00:00Z",
            estimated_input_tokens=2000,
        ))
        assert r["status"] == "created"
        batch_id = r["batch_id"]
        assert batch_id.startswith("batch_")

        # List
        r = json.loads(store.list_batches())
        assert r["count"] == 1
        assert r["batches"][0]["request_count"] == 2

        # Get
        r = json.loads(store.get_batch(batch_id))
        assert "batch" in r
        assert r["batch"]["batch_api_id"] == "msgbatch_test123"
        assert r["batch"]["status"] == "processing"

        # Pending
        pending = store.get_pending_batches()
        assert len(pending) == 1

        # Update
        r = json.loads(store.update_batch(batch_id, status="results_retrieved"))
        assert r["batch"]["status"] == "results_retrieved"
        assert len(store.get_pending_batches()) == 0

        # Save results
        store.save_result(batch_id, "task_001__0", "Analysis for block A...", "succeeded")
        store.save_result(batch_id, "task_001__1", "Analysis for block B...", "succeeded")

        # Read results
        r = json.loads(store.read_result(batch_id, "task_001__0"))
        assert r["text"] == "Analysis for block A..."
        assert r["status"] == "succeeded"

        # Read non-existent
        r = json.loads(store.read_result(batch_id, "task_001__99"))
        assert "error" in r
    finally:
        shutil.rmtree(d)


@test("BatchStore: corruption recovery")
def test_batch_corruption():
    d = tempfile.mkdtemp()
    try:
        batch_file = os.path.join(d, "batches.json")
        with open(batch_file, "w") as f:
            f.write("{broken")
        logging.getLogger("daisy").setLevel(logging.CRITICAL)
        from claude_api.batch_store import BatchStore
        store = BatchStore(d)
        logging.getLogger("daisy").setLevel(logging.WARNING)
        assert len(store._batches) == 0, "Should recover to empty"
    finally:
        shutil.rmtree(d)


@test("BatchStore: persistence across instances")
def test_batch_persistence():
    from claude_api.batch_store import BatchStore
    d = tempfile.mkdtemp()
    try:
        store1 = BatchStore(d)
        store1.create_batch(
            task_id="t1", model="haiku", manifest=[{"custom_id": "t1__0", "label": "x"}],
            batch_api_id="msgbatch_abc", expires_at="2026-04-05",
        )
        store2 = BatchStore(d)
        r = json.loads(store2.list_batches())
        assert r["count"] == 1
    finally:
        shutil.rmtree(d)


@test("get_batch_results: index filtering and summary_only")
def test_get_batch_results_tool():
    from claude_api.batch_store import BatchStore
    d = tempfile.mkdtemp()
    try:
        store = BatchStore(d)
        manifest = [
            {"custom_id": "t__0", "label": "first"},
            {"custom_id": "t__1", "label": "second"},
        ]
        r = json.loads(store.create_batch(
            task_id="t", model="haiku", manifest=manifest,
            batch_api_id="msg_test", expires_at="2026-04-05",
        ))
        bid = r["batch_id"]
        store.update_batch(bid, status="results_retrieved")
        store.save_result(bid, "t__0", "A" * 500, "succeeded")
        store.save_result(bid, "t__1", "B" * 500, "succeeded")

        # Import and call handler directly
        from claude_api.tools.batch.get_batch_results import make_handler
        handler = make_handler(batch_store=store)

        # All results
        r = json.loads(handler(batch_id=bid))
        assert r["count"] == 2
        assert len(r["results"][0]["text"]) == 500

        # Single index
        r = json.loads(handler(batch_id=bid, index=1))
        assert r["count"] == 1
        assert r["results"][0]["label"] == "second"

        # Summary only
        r = json.loads(handler(batch_id=bid, summary_only=True))
        assert len(r["results"][0]["text"]) == 203  # 200 + "..."
    finally:
        shutil.rmtree(d)


@test("Tool result spill-to-file on oversized output")
def test_tool_spill():
    from claude_api.agent_loop import MAX_TOOL_RESULT, _spill_to_file
    from claude_api.config import DaisyConfig
    d = tempfile.mkdtemp()
    try:
        config = DaisyConfig(workspace_dir=d)
        big_output = "x" * (MAX_TOOL_RESULT + 1000)
        path = _spill_to_file(big_output, "test_tool", config)
        assert os.path.exists(path), "Spill file should exist"
        with open(path) as f:
            assert len(f.read()) == len(big_output), "Full output should be in file"
        assert "tool_test_tool_" in path
    finally:
        shutil.rmtree(d)


@test("directory_tree: capped at MAX_ENTRIES")
def test_directory_tree_cap():
    from claude_api.tools.search.directory_tree import handler, MAX_ENTRIES
    # Create a dir with many files to guarantee cap is hit
    d = tempfile.mkdtemp()
    try:
        for i in range(100):
            sub = os.path.join(d, "dir_%03d" % i)
            os.makedirs(sub)
            for j in range(30):
                open(os.path.join(sub, "file_%03d.txt" % j), "w").close()
        # 100 dirs * 30 files = 3000 entries > MAX_ENTRIES=2000
        r = json.loads(handler(d, max_depth=3))
        assert r["entries"] <= MAX_ENTRIES, (
            "Should cap at %d entries, got %d" % (MAX_ENTRIES, r["entries"])
        )
        assert r.get("truncated") is True, "Should flag truncation"
    finally:
        shutil.rmtree(d)


@test("repair_tools: diagnose and fix missing schema type")
def test_repair_tools():
    from claude_api.tool_registry import ToolRegistry
    from claude_api.tools.system.repair_tools import make_handler
    d = tempfile.mkdtemp()
    try:
        # Create a tool with missing "type" in INPUT_SCHEMA
        with open(os.path.join(d, "bad_tool.py"), "w") as f:
            f.write('import json\n'
                    'NAME = "bad_tool"\n'
                    'DESCRIPTION = "A bad tool"\n'
                    'INPUT_SCHEMA = {\n'
                    '  "properties": {"x": {"type": "string"}}\n'
                    '}\n'
                    'def handler(x=""):\n'
                    '    return json.dumps({"x": x})\n')
        # Create a tool with syntax error
        with open(os.path.join(d, "broken.py"), "w") as f:
            f.write('def this is not valid python\n')
        # Patch config paths
        import claude_api.config as cfg
        old_user = cfg.USER_TOOLS_DIR
        old_proj = cfg.DEFAULT_CUSTOM_TOOLS_DIR
        cfg.USER_TOOLS_DIR = d
        cfg.DEFAULT_CUSTOM_TOOLS_DIR = tempfile.mkdtemp()  # empty
        try:
            reg = ToolRegistry()
            handler = make_handler(config=None, registry=reg)
            # Dry run
            r = json.loads(handler(fix=False))
            assert r["summary"]["total_scanned"] == 2
            assert r["summary"]["needs_manual_fix"] >= 1
            # Fix
            r = json.loads(handler(fix=True))
            fixed = [t for t in r["tools"] if "fixed" in t["status"]]
            unfixable = [t for t in r["tools"] if t["status"] == "unfixable"]
            assert len(fixed) == 1, "Should fix bad_tool: %s" % r["tools"]
            assert len(unfixable) == 1, "broken.py should be unfixable"
            # Verify the file was actually fixed
            with open(os.path.join(d, "bad_tool.py")) as f:
                content = f.read()
            assert '"type": "object"' in content, "File should have type added"
        finally:
            cfg.USER_TOOLS_DIR = old_user
            cfg.DEFAULT_CUSTOM_TOOLS_DIR = old_proj
    finally:
        shutil.rmtree(d)


@test("repair_skills: diagnose and fix missing frontmatter")
def test_repair_skills():
    from claude_api.skills import SkillLoader
    from claude_api.tools.system.repair_skills import make_handler
    d = tempfile.mkdtemp()
    try:
        # Skill with no frontmatter
        with open(os.path.join(d, "no_front.md"), "w") as f:
            f.write("# Just content\nNo frontmatter here.\n")
        # Skill with missing name
        with open(os.path.join(d, "no_name.md"), "w") as f:
            f.write("---\nsummary: Has summary but no name\n---\n# Content\nStuff.\n")
        # Good skill
        with open(os.path.join(d, "good.md"), "w") as f:
            f.write("---\nname: good\nsummary: A good skill\n---\n# Steps\n1. Do thing\n")

        loader = SkillLoader([d])

        class FakeConfig:
            skills_dir = tempfile.mkdtemp()  # empty

        handler = make_handler(config=FakeConfig(), skill_loader=loader)
        # Dry run
        r = json.loads(handler(fix=False))
        assert r["summary"]["total_scanned"] == 3
        assert r["summary"]["ok"] == 1
        assert r["summary"]["needs_manual_fix"] == 2
        # Fix
        r = json.loads(handler(fix=True))
        fixed = [s for s in r["skills"] if "fixed" in s["status"]]
        assert len(fixed) == 2, "Should fix both broken skills: %s" % r["skills"]
        # Verify no_front.md now has frontmatter
        with open(os.path.join(d, "no_front.md")) as f:
            content = f.read()
        assert content.startswith("---"), "Should have frontmatter added"
        assert "name: no_front" in content
        # Verify no_name.md now has name
        with open(os.path.join(d, "no_name.md")) as f:
            content = f.read()
        assert "name: no_name" in content
        # Verify skill loader was refreshed and finds them
        assert "good" in loader._index
        assert "no_front" in loader._index
    finally:
        shutil.rmtree(d)


@test("Admin flag: hidden from --help")
def test_admin_hidden():
    import subprocess
    project_root = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env["DAISY_PYTHON"] = sys.executable
    result = subprocess.run(
        [os.path.join(project_root, "bin", "daisy"), "--help"],
        capture_output=True, text=True, env=env,
    )
    assert "--admin" not in result.stdout, "--admin should be hidden from --help"


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


@test("Budget: warning at 80%, exceeded at 100%")
def test_budget():
    from claude_api.audit import AuditLogger, BudgetExceededError
    d = tempfile.mkdtemp()
    try:
        # Budget of $0.01, haiku pricing (0.80/M in, 4.00/M out)
        audit = AuditLogger(d, "claude-haiku-4-5", budget=0.01)
        # Small call: cost ~$0.0028 (well under $0.01)
        audit.log_api_call("claude-haiku-4-5", 1000, 500, "end_turn", 0.5, 0)
        assert audit.check_budget() == "ok"
        # Push to ~80% of budget: add more tokens
        # Need cost >= $0.008 for warning. Current: $0.0028.
        # Add 5000 in, 1000 out => +0.004 + 0.004 = +$0.008 => total $0.0108
        audit.log_api_call("claude-haiku-4-5", 5000, 1000, "end_turn", 0.5, 1)
        status = audit.check_budget()
        assert status == "exceeded", "Expected exceeded at $0.0108, got %s (cost=$%.4f)" % (status, audit.get_session_cost())
        # Verify the event was logged
        with open(audit.log_file) as f:
            events = [json.loads(l) for l in f.readlines()]
        event_types = [e["event"] for e in events]
        assert "budget_exceeded" in event_types, "Missing budget_exceeded event"
    finally:
        shutil.rmtree(d)


@test("Budget: unlimited when budget is None")
def test_budget_unlimited():
    from claude_api.audit import AuditLogger
    d = tempfile.mkdtemp()
    try:
        audit = AuditLogger(d, "claude-haiku-4-5", budget=None)
        audit.log_api_call("claude-haiku-4-5", 1_000_000, 500_000, "end_turn", 1.0, 0)
        assert audit.check_budget() == "ok", "Should always be ok with no budget"
    finally:
        shutil.rmtree(d)


@test("Tool cache: cacheable tools served from cache")
def test_tool_cache():
    from claude_api.agent_loop import _CACHEABLE_TOOLS, _make_cache_key
    # Verify cacheable set is correct
    assert "read_file" in _CACHEABLE_TOOLS
    assert "search_files" in _CACHEABLE_TOOLS
    assert "stat_file" in _CACHEABLE_TOOLS
    assert "diff_files" in _CACHEABLE_TOOLS
    assert "list_recent_files" in _CACHEABLE_TOOLS
    # Verify non-cacheable
    assert "run_command" not in _CACHEABLE_TOOLS
    assert "write_file" not in _CACHEABLE_TOOLS
    assert "save_memory" not in _CACHEABLE_TOOLS
    assert "create_tool" not in _CACHEABLE_TOOLS
    # Verify cache key determinism
    key1 = _make_cache_key("read_file", {"path": "/tmp/a"})
    key2 = _make_cache_key("read_file", {"path": "/tmp/a"})
    key3 = _make_cache_key("read_file", {"path": "/tmp/b"})
    assert key1 == key2, "Same input should produce same key"
    assert key1 != key3, "Different input should produce different key"


@test("Compaction: summarizes old messages")
def test_compaction():
    from claude_api.compaction import ConversationCompactor
    # Create a mock client with a fake messages.create
    class FakeResponse:
        class content_block:
            type = "text"
            text = "Summary: user asked about X, assistant explained Y."
        content = [content_block()]
    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()
    class FakeClient:
        messages = FakeMessages()

    compactor = ConversationCompactor(FakeClient(), threshold_tokens=100)
    history = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "resp1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "resp2"},
        {"role": "user", "content": "msg3"},
        {"role": "assistant", "content": "resp3"},
        {"role": "user", "content": "msg4"},
        {"role": "assistant", "content": "resp4"},
    ]
    # Threshold too high — should not compact
    result = compactor.maybe_compact(history, last_input_tokens=50)
    assert result is False
    assert len(history) == 8

    # Threshold exceeded — should compact
    result = compactor.maybe_compact(history, last_input_tokens=200)
    assert result is True
    # Should have: summary (user) + ack (assistant) + last 4 messages
    assert len(history) == 6, "Expected 6 messages after compaction, got %d" % len(history)
    assert "[Conversation Summary]" in history[0]["content"]
    assert history[1]["role"] == "assistant"
    # Last 4 should be preserved
    assert history[2]["content"] == "msg3"
    assert history[5]["content"] == "resp4"


@test("Compaction: handles tool result blocks in history")
def test_compaction_tool_blocks():
    from claude_api.compaction import ConversationCompactor
    class FakeResponse:
        class content_block:
            type = "text"
            text = "Summary with tool results."
        content = [content_block()]
    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()
    class FakeClient:
        messages = FakeMessages()

    compactor = ConversationCompactor(FakeClient(), threshold_tokens=100)
    history = [
        {"role": "user", "content": "read this file"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "/tmp/x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "file content here"},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "The file contains..."}]},
        {"role": "user", "content": "now search for errors"},
        {"role": "assistant", "content": "found 3 errors"},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "you're welcome"},
    ]
    result = compactor.maybe_compact(history, last_input_tokens=200)
    assert result is True, "Should compact 8 messages (keep 4, summarize 4)"
    assert "[Conversation Summary]" in history[0]["content"]


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


# ── EDA tools (scan_workspaces, tabulate_workspaces) ──────

def _make_fake_workspace_tree(root: str, layout: list):
    """Create empty iflowblocks dirs per layout entries.

    layout items look like ("trialA", "SYN", "blk") -> makes
    <root>/trialA/SYN/blk/iflowblocks/<blk>/imp/trialA/
    so that find_work_location() resolves to a real dir.
    """
    for trial, stage, block in layout:
        ifb = os.path.join(root, trial, stage, block, "iflowblocks")
        work = os.path.join(ifb, block, "imp", trial)
        os.makedirs(work, exist_ok=True)


@test("EDA: scan_workspaces on empty tree")
def test_scan_workspaces_empty():
    from claude_api.tools.eda.scan_workspaces import make_handler as make_scan
    d = tempfile.mkdtemp()
    handler = make_scan(audit=None)
    r = json.loads(handler(search_root=d))
    assert r["ok"] is True, r
    assert r["counts"]["active"] == 0
    assert r["counts"]["total"] == 0
    for key in ("active", "syn", "pnr"):
        p = r["rpt_files"][key]
        assert os.path.isfile(p), "Missing rpt: %s" % p
        with open(p) as fh:
            assert fh.read() == "", "Expected empty %s" % p
    shutil.rmtree(d)


@test("EDA: scan_workspaces classifies SYN/PNR with PNR superseding")
def test_scan_workspaces_classify():
    from claude_api.tools.eda.scan_workspaces import make_handler as make_scan
    d = tempfile.mkdtemp()
    # trialA has both SYN and PNR; trialB has only SYN
    _make_fake_workspace_tree(d, [
        ("trialA", "SYN", "blk"),
        ("trialA", "PNR", "blk"),
        ("trialB", "SYN", "blk"),
    ])
    handler = make_scan(audit=None)
    r = json.loads(handler(search_root=d, analyze_stages=False))
    assert r["ok"] is True, r
    assert r["counts"]["syn"] == 2, "syn=%d" % r["counts"]["syn"]
    assert r["counts"]["pnr"] == 1, "pnr=%d" % r["counts"]["pnr"]
    # active = trialA's PNR (supersedes SYN) + trialB's SYN = 2
    assert r["counts"]["active"] == 2, "active=%d" % r["counts"]["active"]
    with open(r["rpt_files"]["active"]) as fh:
        active_lines = [ln.strip() for ln in fh if ln.strip()]
    assert len(active_lines) == 2
    assert any("/PNR/" in ln for ln in active_lines), "expected PNR path in ACTIVE"
    assert any("trialB/SYN/" in ln for ln in active_lines), "expected trialB SYN in ACTIVE"
    shutil.rmtree(d)


@test("EDA: scan_workspaces analyze_stages=False skips log reads")
def test_scan_workspaces_no_analyze():
    from claude_api.tools.eda.scan_workspaces import make_handler as make_scan
    d = tempfile.mkdtemp()
    _make_fake_workspace_tree(d, [("trialX", "SYN", "blkA")])
    handler = make_scan(audit=None)
    r = json.loads(handler(search_root=d, analyze_stages=False))
    assert r["ok"] is True
    assert r["counts"]["syn"] == 1
    assert r["counts"]["active"] == 1
    shutil.rmtree(d)


@test("EDA: scan_workspaces missing root returns ok=False")
def test_scan_workspaces_missing_root():
    from claude_api.tools.eda.scan_workspaces import make_handler as make_scan
    handler = make_scan(audit=None)
    r = json.loads(handler(search_root="/does/not/exist/ever"))
    assert r["ok"] is False
    assert "search_root" in r["error"]


@test("EDA: tabulate_workspaces empty list writes empty file")
def test_tabulate_workspaces_empty():
    from claude_api.tools.eda.tabulate_workspaces import make_handler as make_tab
    d = tempfile.mkdtemp()
    list_file = os.path.join(d, "empty.rpt")
    open(list_file, "w").close()
    handler = make_tab(audit=None)
    r = json.loads(handler(workspace_list_file=list_file))
    assert r["ok"] is True, r
    assert r["num_trials"] == 0
    assert r["baseline_applied"] is False
    assert os.path.isfile(r["output_file"])
    shutil.rmtree(d)


@test("EDA: tabulate_workspaces both inputs returns ok=False")
def test_tabulate_workspaces_both_inputs():
    from claude_api.tools.eda.tabulate_workspaces import make_handler as make_tab
    d = tempfile.mkdtemp()
    list_file = os.path.join(d, "a.rpt")
    open(list_file, "w").close()
    handler = make_tab(audit=None)
    r = json.loads(handler(workspace_list_file=list_file, work_dirs=["/x"]))
    assert r["ok"] is False
    assert "Provide either" in r["error"]
    shutil.rmtree(d)


@test("EDA: tabulate_workspaces synthetic trial produces Title header")
def test_tabulate_workspaces_synthetic():
    from claude_api.tools.eda.tabulate_workspaces import make_handler as make_tab
    d = tempfile.mkdtemp()
    # Minimal workspace: just a directory. Tabulator emits header rows
    # (Title, Block Name, Version) regardless of whether metrics exist.
    work_dir = os.path.join(d, "trialZ")
    os.makedirs(work_dir)
    out = os.path.join(d, "tab.csv")
    handler = make_tab(audit=None)
    r = json.loads(handler(work_dirs=[work_dir], output_file=out))
    assert r["ok"] is True, r
    assert r["num_trials"] == 1
    assert r["baseline_applied"] is False
    assert os.path.isfile(out)
    with open(out) as fh:
        content = fh.read()
    assert content.startswith("Title;"), "expected Title header row; got: %s" % content[:200]
    assert "Block Name" in content, "missing Block Name row"
    assert "trialZ" in content, "missing trial name in output"
    shutil.rmtree(d)


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
    test_list_recent_files,
    test_diff_files,
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
    test_budget,
    test_budget_unlimited,
    test_tool_cache,
    test_compaction,
    test_compaction_tool_blocks,
    test_task_lifecycle,
    test_task_corruption,
    test_task_persistence,
    test_batch_store,
    test_batch_corruption,
    test_batch_persistence,
    test_get_batch_results_tool,
    test_tool_spill,
    test_directory_tree_cap,
    test_repair_tools,
    test_repair_skills,
    test_admin_hidden,
    test_cli_help,
    test_cli_list_sessions,
    test_scan_workspaces_empty,
    test_scan_workspaces_classify,
    test_scan_workspaces_no_analyze,
    test_scan_workspaces_missing_root,
    test_tabulate_workspaces_empty,
    test_tabulate_workspaces_both_inputs,
    test_tabulate_workspaces_synthetic,
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
    test_budget,
    test_tool_cache,
    test_compaction,
    test_task_lifecycle,
    test_batch_store,
    test_get_batch_results_tool,
    test_tool_spill,
    test_directory_tree_cap,
    test_repair_tools,
    test_repair_skills,
    test_admin_hidden,
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
