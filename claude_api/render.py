"""Optional rich rendering for assistant replies.

Falls back to plain print if rich is not vendored. Rich and its deps
(markdown-it-py, mdurl, pygments) live in vendor/wheels/ and are unpacked
into vendor/lib/ by vendor/install.sh; the import below succeeds when they
are present and silently fails when they are not.
"""
from __future__ import annotations

import sys

try:
    from rich.console import Console
    from rich.markdown import Markdown
    _console = Console()
    HAS_RICH = True
except ImportError:
    _console = None
    HAS_RICH = False

# Set by cli.py from --no-rich. When True, render_assistant always plain-prints
# even if rich is available. Useful for piping output to a file.
_force_plain = False


def set_force_plain(value: bool) -> None:
    global _force_plain
    _force_plain = value


def render_assistant(text: str) -> None:
    """Print an assistant reply, with markdown rendering if available."""
    if HAS_RICH and _console is not None and not _force_plain:
        _console.print(Markdown(text))
    else:
        print(text)


def render_stream_chunk(text: str) -> None:
    """Print a streamed text delta. We deliberately do NOT use rich here:
    rich's Live renderer doesn't compose well with our line-oriented streaming
    or with the [Daisy] running ... stderr lines emitted between tool rounds.
    Streaming + plain text is the right tradeoff; users who want markdown
    rendering should pass --no-stream."""
    sys.stdout.write(text)
    sys.stdout.flush()


def end_stream() -> None:
    """Emit a trailing newline after a stream closes (only if anything was written)."""
    sys.stdout.write("\n")
    sys.stdout.flush()
