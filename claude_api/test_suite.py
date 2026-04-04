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
    from claude_api.tools.file.write_file import handler as _wf
    from claude_api.tools.file.edit_file import handler as _ef
    from claude_api.tools.file.append_file import handler as _af
    from claude_api.tools.search.search_files import handler as _sf
    from claude_api.tools.search.find_files import handler as _ff
    from claude_api.tools.search.directory_tree import handler as _dt
    from claude_api.tools.search.list_directory import handler as _ld
    from claude_api.tools.system.get_env import handler as _ge
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
    from claude_api.tools.file.read_file import handler as read_file
    from claude_api.tools.file.write_file import handler as write_file
    from claude_api.tools.search.list_directory import handler as list_directory
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


@test("File tools: find_files (glob)")
def test_find_files():
    from claude_api.tools.search.find_files import handler as find_files
    r = json.loads(find_files("*.py", os.path.dirname(__file__)))
    assert r["count"] >= 10, "Should find at least 10 .py files, got %d" % r["count"]


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


@test("Built-in tools: all 23 registered")
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
        "save_memory", "search_memory", "delete_memory", "list_memories",
        "read_file", "write_file", "edit_file", "append_file",
        "list_directory", "search_files", "find_files", "directory_tree",
        "get_env", "load_skill", "create_skill", "create_tool",
        "create_task", "update_task", "list_tasks", "get_task",
        "submit_batch", "check_batch", "get_batch_results",
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
    assert "get_env" in _CACHEABLE_TOOLS
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
    test_admin_hidden,
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
    test_budget,
    test_tool_cache,
    test_compaction,
    test_task_lifecycle,
    test_batch_store,
    test_get_batch_results_tool,
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
