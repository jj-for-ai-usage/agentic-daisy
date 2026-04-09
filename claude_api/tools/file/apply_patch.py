"""Tool: apply_patch -- apply a unified diff to one or more files.

Parses unified diff format (output of 'diff -u' or 'git diff'), locates
each hunk in the target file (with configurable fuzz), and writes the
result atomically: if any hunk in any file fails to locate, NOTHING is
written. Supports dry_run mode for safe preview.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import List, Optional, Tuple

NAME = "apply_patch"
DESCRIPTION = (
    "Apply a unified diff (diff -u / git diff) to one or more files. Atomic: "
    "if any hunk in any file fails to locate, NOTHING is written. Supports "
    "fuzz matching (default 3 lines of drift tolerance). Use dry_run=true "
    "to preview changes without writing. Backs up originals to "
    ".daisy/workspace/patch_backup_<ts>/ on successful apply. "
    "Use this for multi-hunk or multi-file changes; for a single logical "
    "find/replace, edit_file is simpler."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patch": {
            "type": "string",
            "description": "Unified diff string (output of 'diff -u' or 'git diff').",
        },
        "base_dir": {
            "type": "string",
            "description": "Base directory paths in the patch are relative to. Default: current directory.",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, report what would change without writing. Default false.",
        },
        "fuzz": {
            "type": "integer",
            "description": "Max lines of drift tolerance per hunk. Default 3.",
        },
    },
    "required": ["patch"],
}

DEFAULT_FUZZ = 3
_HUNK_HEADER_RX = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


class _Hunk:
    def __init__(self, old_start: int, old_count: int, new_start: int, new_count: int):
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.lines: List[Tuple[str, str]] = []  # (tag, text) tag in {' ', '-', '+'}

    def old_lines(self) -> List[str]:
        return [text for tag, text in self.lines if tag in (" ", "-")]

    def new_lines(self) -> List[str]:
        return [text for tag, text in self.lines if tag in (" ", "+")]


class _FilePatch:
    def __init__(self, old_path: str, new_path: str):
        self.old_path = old_path
        self.new_path = new_path
        self.hunks: List[_Hunk] = []
        self.is_new_file = old_path == "/dev/null"
        self.is_deletion = new_path == "/dev/null"


def _parse_patch(patch: str) -> List[_FilePatch]:
    """Parse a unified diff into a list of _FilePatch objects."""
    files: List[_FilePatch] = []
    current_file: Optional[_FilePatch] = None
    current_hunk: Optional[_Hunk] = None

    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip git-style "diff --git" headers and "index " lines
        if line.startswith("diff --git") or line.startswith("index "):
            i += 1
            continue
        # File headers
        if line.startswith("--- "):
            old = line[4:].split("\t")[0]
            # strip a/ or b/ prefix if present (git style)
            if old.startswith("a/"):
                old = old[2:]
            # Expect +++ on next line
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                new = lines[i + 1][4:].split("\t")[0]
                if new.startswith("b/"):
                    new = new[2:]
                current_file = _FilePatch(old, new)
                files.append(current_file)
                current_hunk = None
                i += 2
                continue
            else:
                i += 1
                continue

        if line.startswith("@@"):
            m = _HUNK_HEADER_RX.match(line)
            if not m or current_file is None:
                i += 1
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_hunk = _Hunk(old_start, old_count, new_start, new_count)
            current_file.hunks.append(current_hunk)
            i += 1
            continue

        if current_hunk is not None and line and line[0] in (" ", "+", "-"):
            # Collect hunk body lines
            current_hunk.lines.append((line[0], line[1:]))
            i += 1
            continue

        # Anything else -- ignore (blank lines between hunks, etc.)
        i += 1

    return files


def _locate_hunk(
    file_lines: List[str], hunk: _Hunk, fuzz: int
) -> Optional[int]:
    """Return 0-indexed start line in file_lines where the hunk's old content
    is found, or None if not found within fuzz tolerance.
    """
    old = hunk.old_lines()
    if not old:
        # Pure insertion at a specific position
        return max(0, hunk.old_start - 1)

    target_start = hunk.old_start - 1  # Convert to 0-indexed

    def _matches_at(pos: int) -> bool:
        if pos < 0 or pos + len(old) > len(file_lines):
            return False
        for j, expected in enumerate(old):
            if file_lines[pos + j] != expected:
                return False
        return True

    # Try exact position first
    if _matches_at(target_start):
        return target_start

    # Fuzz scan: ±fuzz lines around the target
    for delta in range(1, fuzz + 1):
        for candidate in (target_start - delta, target_start + delta):
            if _matches_at(candidate):
                return candidate

    # Last-resort: scan the whole file (expensive but bounded)
    for pos in range(len(file_lines) - len(old) + 1):
        if _matches_at(pos):
            return pos

    return None


def _apply_hunks(file_lines: List[str], hunks: List[_Hunk], fuzz: int) -> Tuple[Optional[List[str]], List[dict]]:
    """Return (new_lines, errors). If errors non-empty, new_lines is None."""
    # Apply hunks in reverse order so earlier hunks' line numbers remain valid
    # relative to the ORIGINAL file. We need to locate each hunk in the original
    # first, then apply them back-to-front on a working copy.
    locations: List[int] = []
    errors: List[dict] = []

    for idx, hunk in enumerate(hunks):
        pos = _locate_hunk(file_lines, hunk, fuzz)
        if pos is None:
            errors.append({
                "hunk_index": idx,
                "old_start": hunk.old_start,
                "error": "hunk context not found in file (even with fuzz %d)" % fuzz,
            })
            locations.append(-1)
        else:
            locations.append(pos)

    if errors:
        return None, errors

    # Sort by descending location so we can splice from the end without shifting earlier indices
    order = sorted(range(len(hunks)), key=lambda i: locations[i], reverse=True)
    working = list(file_lines)
    for idx in order:
        pos = locations[idx]
        hunk = hunks[idx]
        old_len = len(hunk.old_lines())
        new_content = hunk.new_lines()
        working[pos : pos + old_len] = new_content

    return working, []


def _find_file(base_dir: str, rel_path: str) -> Optional[str]:
    """Resolve a patch-relative path to an absolute path."""
    if rel_path == "/dev/null":
        return None
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(base_dir, rel_path)


def handler(
    patch: str,
    base_dir: str = ".",
    dry_run: bool = False,
    fuzz: int = DEFAULT_FUZZ,
) -> str:
    base_dir = os.path.expanduser(base_dir)
    fuzz = max(0, fuzz)

    try:
        file_patches = _parse_patch(patch)
    except Exception as exc:
        return json.dumps({"status": "failed", "error": "parse error: %s" % exc})

    if not file_patches:
        return json.dumps({
            "status": "failed",
            "error": "no file patches found; is this a unified diff?",
        })

    # Stage all changes in memory; only write on success
    staged: List[dict] = []  # [{abs_path, new_content_or_None, action, hunks_applied}]
    errors: List[dict] = []

    for fp in file_patches:
        abs_path = _find_file(base_dir, fp.new_path if fp.is_new_file else fp.old_path)
        entry = {
            "path": fp.new_path if fp.is_new_file else fp.old_path,
            "abs_path": abs_path,
            "action": None,
            "hunks_applied": 0,
            "new_content": None,
        }

        if fp.is_deletion:
            if abs_path and not os.path.isfile(abs_path):
                errors.append({"path": entry["path"], "error": "cannot delete; file not found"})
                continue
            entry["action"] = "delete"
            entry["hunks_applied"] = len(fp.hunks)
            staged.append(entry)
            continue

        if fp.is_new_file:
            # New file: concat the '+' lines across all hunks
            new_lines = []
            for h in fp.hunks:
                new_lines.extend(h.new_lines())
            entry["action"] = "create"
            entry["new_content"] = "\n".join(new_lines) + ("\n" if new_lines else "")
            entry["hunks_applied"] = len(fp.hunks)
            staged.append(entry)
            continue

        # Modify existing file
        if not abs_path or not os.path.isfile(abs_path):
            errors.append({"path": entry["path"], "error": "file not found"})
            continue
        try:
            with open(abs_path, "r", errors="replace") as f:
                original = f.read()
        except Exception as exc:
            errors.append({"path": entry["path"], "error": "read failed: %s" % exc})
            continue

        file_lines = original.splitlines()
        new_lines, hunk_errors = _apply_hunks(file_lines, fp.hunks, fuzz)
        if hunk_errors:
            for he in hunk_errors:
                he["path"] = entry["path"]
            errors.extend(hunk_errors)
            continue

        # Preserve trailing newline behavior of original
        trailing = "\n" if original.endswith("\n") else ""
        entry["action"] = "modify"
        entry["new_content"] = "\n".join(new_lines) + trailing
        entry["hunks_applied"] = len(fp.hunks)
        staged.append(entry)

    if errors:
        return json.dumps({
            "status": "failed",
            "reason": "one or more hunks did not apply; no files modified",
            "errors": errors,
            "files_would_change": [e["path"] for e in staged],
        })

    if dry_run:
        return json.dumps({
            "status": "dry_run",
            "files_would_change": [
                {"path": e["path"], "action": e["action"], "hunks_applied": e["hunks_applied"]}
                for e in staged
            ],
            "hunks_applied_total": sum(e["hunks_applied"] for e in staged),
        })

    # Commit: back up originals, then write
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(base_dir, ".daisy", "workspace", "patch_backup_%s" % ts)
    os.makedirs(backup_dir, exist_ok=True)

    written: List[dict] = []
    try:
        for entry in staged:
            abs_path = entry["abs_path"]
            rel = entry["path"]
            # Back up if the file exists
            if abs_path and os.path.isfile(abs_path):
                backup_path = os.path.join(backup_dir, rel)
                os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
                shutil.copy2(abs_path, backup_path)

            if entry["action"] == "delete":
                os.remove(abs_path)
            elif entry["action"] == "create":
                os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
                with open(abs_path, "w") as f:
                    f.write(entry["new_content"])
            elif entry["action"] == "modify":
                with open(abs_path, "w") as f:
                    f.write(entry["new_content"])
            written.append({
                "path": rel,
                "action": entry["action"],
                "hunks_applied": entry["hunks_applied"],
            })
    except Exception as exc:
        return json.dumps({
            "status": "partial_failure",
            "error": "write failed after %d of %d files: %s" % (len(written), len(staged), exc),
            "written": written,
            "backup_dir": backup_dir,
        })

    return json.dumps({
        "status": "applied",
        "files_changed": written,
        "hunks_applied_total": sum(e["hunks_applied"] for e in staged),
        "backup_dir": backup_dir,
    })
