"""Tool: edit_file — surgical find-and-replace in a file."""
from __future__ import annotations
import difflib
import json
import os

NAME = "edit_file"
DESCRIPTION = (
    "Surgical find-and-replace in a file. Only changes the matched text — "
    "much cheaper than rewriting the whole file with write_file. The "
    "old_string must match exactly once. On 0- or multi-match failures, "
    "returns near-miss lines so you can widen or narrow the match on retry."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to edit"},
        "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
        "new_string": {"type": "string", "description": "Replacement text"},
    },
    "required": ["path", "old_string", "new_string"],
}

_NEAR_MISS_LIMIT = 3


def _fuzzy_hints(content: str, needle: str) -> list:
    """Return up to 3 lines from content that are most similar to needle's
    first line, as hints for retrying the edit with more/different context."""
    needle_first = needle.splitlines()[0] if needle else ""
    if not needle_first:
        return []
    candidates = [ln for ln in content.splitlines() if ln.strip()]
    # Rank by sequence similarity; cheap on typical file sizes.
    scored = [
        (difflib.SequenceMatcher(None, needle_first, ln).ratio(), ln)
        for ln in candidates
    ]
    scored.sort(reverse=True)
    return [ln for ratio, ln in scored[:_NEAR_MISS_LIMIT] if ratio > 0.5]


def handler(path: str, old_string: str, new_string: str) -> str:
    path = os.path.expanduser(path)
    try:
        with open(path, "r") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return json.dumps({
                "ok": False,
                "error": "old_string not found in file",
                "path": path,
                "matches": 0,
                "near_miss_lines": _fuzzy_hints(content, old_string),
            })
        if count > 1:
            # Find line numbers of each match to help Claude pick distinguishing context
            locations = []
            idx = 0
            while True:
                idx = content.find(old_string, idx)
                if idx == -1 or len(locations) >= 5:
                    break
                line_no = content.count("\n", 0, idx) + 1
                locations.append(line_no)
                idx += 1
            return json.dumps({
                "ok": False,
                "error": "old_string matches %d times — provide more context to make it unique" % count,
                "path": path,
                "matches": count,
                "match_line_numbers": locations,
            })
        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        return json.dumps({"ok": True, "status": "edited", "path": path, "replacements": 1})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "path": path})
