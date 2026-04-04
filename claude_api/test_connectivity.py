#!/usr/bin/env python3
"""
Agentic Daisy — Claude API Connectivity Test
----------------------------------------------
Validates that the Python environment can reach the Anthropic API
from within a data-protected environment.

Invocation (via bin/daisy-test wrapper):
    bin/daisy-test
    bin/daisy-test --api-key 'sk-ant-...'
    bin/daisy-test --message "Hello from the chamber" --debug
    bin/daisy-test --model claude-sonnet-4-5-20250514

The API key can be provided via --api-key or the ANTHROPIC_API_KEY
environment variable.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# ---------------------------------------------------------------------------
# sys.path setup — ensure vendored libs are importable
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import anthropic

LOG = logging.getLogger("daisy-test")

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MESSAGE = "testing"
MAX_TOKENS = 256


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Test Claude API connectivity from a data-protected environment.",
    )
    ap.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (overrides ANTHROPIC_API_KEY env var)",
    )
    ap.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    ap.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help=f'Message to send (default: "{DEFAULT_MESSAGE}")',
    )
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS,
        help=f"Max response tokens (default: {MAX_TOKENS})",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # --- Validate API key ---------------------------------------------------
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        LOG.error(
            "No API key provided. "
            "Pass --api-key or set env var:  "
            "export ANTHROPIC_API_KEY='sk-ant-...'  (bash)  |  "
            "setenv ANTHROPIC_API_KEY 'sk-ant-...'  (csh/tcsh)"
        )
        sys.exit(1)

    LOG.info("API key found (ends with ...%s)", api_key[-4:])
    LOG.info("Model   : %s", args.model)
    LOG.info("Message : %s", args.message)

    # --- Make API call ------------------------------------------------------
    client = anthropic.Anthropic(api_key=api_key)

    try:
        LOG.info("Sending request ...")
        t0 = time.time()

        response = client.messages.create(
            model=args.model,
            max_tokens=args.max_tokens,
            messages=[{"role": "user", "content": args.message}],
        )

        elapsed = time.time() - t0

        # --- Display result -------------------------------------------------
        text = response.content[0].text if response.content else "(empty)"
        print()
        print("=" * 60)
        print("  CLAUDE API TEST — SUCCESS")
        print("=" * 60)
        print(f"  Model        : {response.model}")
        print(f"  Input tokens : {response.usage.input_tokens}")
        print(f"  Output tokens: {response.usage.output_tokens}")
        print(f"  Stop reason  : {response.stop_reason}")
        print(f"  Latency      : {elapsed:.2f}s")
        print("-" * 60)
        print(f"  Response:\n\n{text}")
        print("=" * 60)
        print()

    except anthropic.AuthenticationError as exc:
        LOG.error("Authentication failed — check your API key.")
        LOG.error("Detail: %s", exc)
        sys.exit(2)

    except anthropic.APIConnectionError as exc:
        LOG.error("Cannot reach the Anthropic API — network or proxy issue.")
        LOG.error("Detail: %s", exc)
        sys.exit(3)

    except Exception as exc:
        LOG.error("Unexpected error: %s", exc)
        sys.exit(4)


if __name__ == "__main__":
    main()
