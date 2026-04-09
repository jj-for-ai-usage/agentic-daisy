"""Search and navigation tools.

search_files, find_files, directory_tree, list_directory, grep_large_file,
file_summary, extract_section, compare_files.
"""
from __future__ import annotations

from . import (
    search_files, find_files, directory_tree, list_directory,
    grep_large_file, file_summary, extract_section, compare_files,
)

ALL_TOOLS = [
    search_files, find_files, directory_tree, list_directory,
    grep_large_file, file_summary, extract_section, compare_files,
]


def register(config, registry, **kwargs):
    for mod in ALL_TOOLS:
        registry.register(mod.NAME, mod.DESCRIPTION, mod.INPUT_SCHEMA, mod.handler)
