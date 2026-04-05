"""Agentic Daisy -- Skill/playbook loader (summary index + on-demand loading)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Union

LOG = logging.getLogger("daisy")


class SkillLoader:
    """Loads skill .md files from one or more directories. Parses frontmatter
    for the index (name, summary, trigger) and serves full content on demand.

    When multiple directories are given, they are scanned in order -- later
    directories override earlier ones for the same skill name. This lets
    project-level skills (.daisy/skills/) override user-level (~/.daisy/skills/).
    """

    def __init__(self, skills_dirs: Union[str, List[str]]) -> None:
        if isinstance(skills_dirs, str):
            skills_dirs = [skills_dirs]
        self.skills_dirs = skills_dirs
        self._index: Dict[str, Dict[str, str]] = {}  # name -> {summary, trigger, file}
        self._scan_all()

    def _scan_all(self) -> None:
        """Scan all directories and build the merged index."""
        self._index.clear()
        for d in self.skills_dirs:
            if os.path.isdir(d):
                self._scan_dir(d)

    def _scan_dir(self, directory: str) -> None:
        """Scan a single directory for .md skill files."""
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(directory, fname)
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

    def refresh(self) -> None:
        """Re-scan all directories. Call after creating a new skill."""
        self._scan_all()

    @staticmethod
    def _parse_frontmatter(path: str) -> Dict[str, str]:
        """Read YAML-like frontmatter between --- delimiters."""
        meta: Dict[str, str] = {}
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
            # Strip frontmatter -- return only the body
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            return json.dumps({"name": name, "content": content})
        except Exception as exc:
            return json.dumps({"error": str(exc), "name": name})
