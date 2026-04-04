"""Agentic Daisy — File read/write/list/search/find tools."""
from __future__ import annotations

import fnmatch
import json
import os
import re
from typing import List, Optional

MAX_READ_SIZE = 100_000  # characters
MAX_SEARCH_RESULTS = 200  # matches returned by search_files
MAX_FIND_RESULTS = 500  # files returned by find_files
MAX_TREE_DEPTH = 5  # levels for recursive list_directory


def read_file(path: str) -> str:
    """Read and return file contents. Truncates at MAX_READ_SIZE."""
    path = os.path.expanduser(path)
    try:
        size = os.path.getsize(path)
        with open(path, "r") as f:
            content = f.read(MAX_READ_SIZE + 1)
        truncated = len(content) > MAX_READ_SIZE
        if truncated:
            content = content[:MAX_READ_SIZE]
        return json.dumps({
            "path": path,
            "content": content,
            "truncated": truncated,
            "size_bytes": size,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    path = os.path.expanduser(path)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return json.dumps({
            "status": "written",
            "path": path,
            "bytes": len(content),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Surgical find-and-replace in a file. Only changes the matched text."""
    path = os.path.expanduser(path)
    try:
        with open(path, "r") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return json.dumps({
                "error": "old_string not found in file",
                "path": path,
            })
        if count > 1:
            return json.dumps({
                "error": "old_string matches %d times — provide more context to make it unique" % count,
                "path": path,
                "matches": count,
            })
        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        return json.dumps({
            "status": "edited",
            "path": path,
            "replacements": 1,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def append_file(path: str, content: str) -> str:
    """Append content to the end of a file. Creates the file if it doesn't exist."""
    path = os.path.expanduser(path)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a") as f:
            f.write(content)
        return json.dumps({
            "status": "appended",
            "path": path,
            "bytes_added": len(content),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def get_env() -> str:
    """Return a snapshot of the current system environment."""
    import platform
    import shutil
    info = {
        "hostname": platform.node(),
        "user": os.environ.get("USER", os.environ.get("LOGNAME", "unknown")),
        "cwd": os.getcwd(),
        "python_version": platform.python_version(),
        "os": "%s %s" % (platform.system(), platform.release()),
        "arch": platform.machine(),
    }
    # Disk usage for cwd
    try:
        usage = shutil.disk_usage(os.getcwd())
        info["disk_total_gb"] = round(usage.total / (1024 ** 3), 1)
        info["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
        info["disk_used_pct"] = round((usage.used / usage.total) * 100, 1)
    except OSError:
        pass
    return json.dumps(info)


def list_directory(path: str = ".") -> str:
    """List directory contents with type and size info."""
    path = os.path.expanduser(path)
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            entry = {
                "name": name,
                "type": "dir" if os.path.isdir(full) else "file",
            }
            if entry["type"] == "file":
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    pass
            entries.append(entry)
        return json.dumps({
            "path": path,
            "count": len(entries),
            "entries": entries,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})


def search_files(
    pattern: str,
    path: str = ".",
    include: str = "",
    context_lines: int = 0,
) -> str:
    """Search file contents by regex pattern. Returns structured matches."""
    path = os.path.expanduser(path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"error": "Invalid regex: %s" % exc, "pattern": pattern})

    results = []  # type: List[dict]
    files_searched = 0

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            # Skip hidden dirs and common noise
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fname in sorted(filenames):
                # Optional glob filter on filename
                if include and not fnmatch.fnmatch(fname, include):
                    continue
                fpath = os.path.join(dirpath, fname)
                # Skip binary / large files
                try:
                    size = os.path.getsize(fpath)
                    if size > 1_000_000:  # skip files > 1MB
                        continue
                except OSError:
                    continue

                try:
                    with open(fpath, "r", errors="ignore") as f:
                        file_lines = f.readlines()
                except (OSError, UnicodeDecodeError):
                    continue

                files_searched += 1
                for i, line in enumerate(file_lines):
                    if regex.search(line):
                        match = {
                            "file": fpath,
                            "line_number": i + 1,
                            "line": line.rstrip("\n"),
                        }
                        if context_lines > 0:
                            start = max(0, i - context_lines)
                            end = min(len(file_lines), i + context_lines + 1)
                            match["context"] = [
                                ln.rstrip("\n") for ln in file_lines[start:end]
                            ]
                        results.append(match)
                        if len(results) >= MAX_SEARCH_RESULTS:
                            return json.dumps({
                                "pattern": pattern,
                                "matches": len(results),
                                "truncated": True,
                                "files_searched": files_searched,
                                "results": results,
                            })

        return json.dumps({
            "pattern": pattern,
            "matches": len(results),
            "truncated": False,
            "files_searched": files_searched,
            "results": results,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "pattern": pattern, "path": path})


def find_files(pattern: str, path: str = ".") -> str:
    """Find files by glob pattern (e.g. '*.py', '*.conf'). Recursive."""
    path = os.path.expanduser(path)
    results = []  # type: List[str]

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fname in sorted(filenames):
                if fnmatch.fnmatch(fname, pattern):
                    results.append(os.path.join(dirpath, fname))
                    if len(results) >= MAX_FIND_RESULTS:
                        return json.dumps({
                            "pattern": pattern,
                            "path": path,
                            "count": len(results),
                            "truncated": True,
                            "files": results,
                        })

        return json.dumps({
            "pattern": pattern,
            "path": path,
            "count": len(results),
            "truncated": False,
            "files": results,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "pattern": pattern, "path": path})


def directory_tree(path: str = ".", max_depth: int = 3) -> str:
    """Recursive directory tree with depth limit."""
    path = os.path.expanduser(path)
    max_depth = max(1, min(max_depth, MAX_TREE_DEPTH))

    def _walk(current: str, depth: int) -> list:
        if depth > max_depth:
            return []
        try:
            items = []
            for name in sorted(os.listdir(current)):
                if name.startswith(".") or name in ("__pycache__", "node_modules"):
                    continue
                full = os.path.join(current, name)
                if os.path.isdir(full):
                    children = _walk(full, depth + 1)
                    items.append({"name": name, "type": "dir", "children": children})
                else:
                    entry = {"name": name, "type": "file"}
                    try:
                        entry["size"] = os.path.getsize(full)
                    except OSError:
                        pass
                    items.append(entry)
            return items
        except OSError:
            return []

    try:
        tree = _walk(path, 1)
        return json.dumps({"path": path, "max_depth": max_depth, "tree": tree})
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": path})
