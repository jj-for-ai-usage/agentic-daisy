"""Plain-text rendering for assistant replies.

Previously supported optional rich-library markdown rendering, but that added
4 vendored wheels (rich, markdown-it-py, mdurl, pygments) for marginal value.
Assistant replies now print as plain text; pipe through an external renderer
if you want formatted output.
"""
from __future__ import annotations

import sys


def render_assistant(text: str) -> None:
    """Print an assistant reply as plain text."""
    print(text)


def render_stream_chunk(text: str) -> None:
    """Print a streamed text delta."""
    sys.stdout.write(text)
    sys.stdout.flush()


def end_stream() -> None:
    """Emit a trailing newline after a stream closes."""
    sys.stdout.write("\n")
    sys.stdout.flush()
