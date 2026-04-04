"""Agentic Daisy — Skill/playbook loader (summary index + on-demand loading)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

LOG = logging.getLogger("daisy")


class SkillLoader:
    """Loads skill .md files from a directory. Parses frontmatter for the
    index (name, summary, trigger) and serves full content on demand."""

    def __init__(self, skills_dir: str) -> None:
        self.skills_dir = skills_dir
        self._index: Dict[str, Dict[str, str]] = {}  # name -> {summary, trigger, file}
        if os.path.isdir(skills_dir):
            self._scan()

    def _scan(self) -> None:
        """Scan .md files and parse --- frontmatter for index metadata."""
        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(self.skills_dir, fname)
            try:
                meta = self._parse_frontmatter(path)
                if meta.get("name"):
                    self._index[meta["name"]] = {
                        "summary": meta.get("summary", ""),
                        "trigger": meta.get("trigger", ""),
                        "file": path,
                    }
            except Exception as exc:
                LOG.warning("Skipping skill file %s: %s", fname, exc)

    @staticmethod
    def _parse_frontmatter(path: str) -> Dict[str, str]:
        """Read YAML-like frontmatter between --- delimiters."""
        meta = {}  # type: Dict[str, str]
        with open(path, "r") as f:
            lines = f.readlines()
        if not lines or lines[0].strip() != "---":
            return meta
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip()] = val.strip().strip('"').strip("'")
        return meta

    def get_index_for_prompt(self) -> str:
        """Return a formatted skill index for the system prompt.
        Returns empty string if no skills are available."""
        if not self._index:
            return ""
        lines = [
            "\n## Available Skills (Playbooks)",
            "Call load_skill(name) to get the full procedure before executing.",
        ]
        for name, info in self._index.items():
            lines.append("- **%s**: %s" % (name, info["summary"]))
        return "\n".join(lines) + "\n"

    def load_skill(self, name: str) -> str:
        """Load the full content of a named skill. Tool handler."""
        if name not in self._index:
            available = ", ".join(sorted(self._index.keys())) or "none"
            return json.dumps({
                "error": "Skill '%s' not found. Available: %s" % (name, available),
            })
        path = self._index[name]["file"]
        try:
            with open(path, "r") as f:
                content = f.read()
            # Strip frontmatter — return only the body
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            return json.dumps({"name": name, "content": content})
        except Exception as exc:
            return json.dumps({"error": str(exc), "name": name})
