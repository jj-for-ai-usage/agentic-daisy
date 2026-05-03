#!/bin/bash
# Unpack vendored dependencies for offline / air-gapped use.
#
# Usage (one-time, after git clone/pull):
#   bash vendor/install.sh
#
# This installs:
#   1. Python wheels  -> vendor/lib/        (importable via PYTHONPATH)
#   2. Node.js        -> vendor/node-dist/  (self-contained runtime)
#   3. Claude Code    -> vendor/claude-dist/ (CLI for Pro/Max/Console accounts)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEELS_DIR="$SCRIPT_DIR/wheels"
LIB_DIR="$SCRIPT_DIR/lib"
NODE_TARBALL_DIR="$SCRIPT_DIR/node"
NODE_DIST_DIR="$SCRIPT_DIR/node-dist"
CLAUDE_TARBALL_DIR="$SCRIPT_DIR/claude"
CLAUDE_DIST_DIR="$SCRIPT_DIR/claude-dist"

# ---------------------------------------------------------------------------
# 1. Python wheels
# ---------------------------------------------------------------------------
if [ ! -d "$WHEELS_DIR" ] || [ -z "$(ls "$WHEELS_DIR"/*.whl 2>/dev/null)" ]; then
    echo "ERROR: No wheel files found in $WHEELS_DIR"
    exit 1
fi

echo "[1/3] Unpacking Python wheels into $LIB_DIR ..."
mkdir -p "$LIB_DIR"
count=0
for whl in "$WHEELS_DIR"/*.whl; do
    name="$(basename "$whl")"
    unzip -o -q "$whl" -d "$LIB_DIR"
    count=$((count + 1))
    echo "  [$count] $name"
done
echo "      $count wheels unpacked."
echo ""

# ---------------------------------------------------------------------------
# 2. Node.js runtime
# ---------------------------------------------------------------------------
NODE_TARBALL="$(ls "$NODE_TARBALL_DIR"/node-v*-linux-x64.tar.xz 2>/dev/null | head -1)"
if [ -z "$NODE_TARBALL" ]; then
    echo "WARNING: No Node.js tarball found in $NODE_TARBALL_DIR -- skipping Node install."
    echo "         Claude CLI will NOT be available."
else
    echo "[2/3] Extracting Node.js from $(basename "$NODE_TARBALL") ..."
    rm -rf "$NODE_DIST_DIR"
    mkdir -p "$NODE_DIST_DIR"
    # Strip the leading node-vXX.X.X-linux-x64/ directory so binaries land at vendor/node-dist/bin/
    tar -xJf "$NODE_TARBALL" -C "$NODE_DIST_DIR" --strip-components=1
    echo "      Node $("$NODE_DIST_DIR/bin/node" --version) installed at $NODE_DIST_DIR"
    echo ""
fi

# ---------------------------------------------------------------------------
# 3. Claude Code CLI
# ---------------------------------------------------------------------------
WRAPPER_TGZ="$(ls "$CLAUDE_TARBALL_DIR"/anthropic-ai-claude-code-[0-9]*.tgz 2>/dev/null | head -1)"
NATIVE_TGZ="$(ls "$CLAUDE_TARBALL_DIR"/anthropic-ai-claude-code-linux-x64-*.tgz 2>/dev/null | head -1)"

if [ -z "$WRAPPER_TGZ" ] || [ -z "$NATIVE_TGZ" ]; then
    echo "WARNING: Claude Code tarballs missing in $CLAUDE_TARBALL_DIR -- skipping."
    echo "         Expected:"
    echo "           anthropic-ai-claude-code-<ver>.tgz             (wrapper)"
    echo "           anthropic-ai-claude-code-linux-x64-<ver>.tgz   (native binary)"
elif [ -z "$NODE_TARBALL" ]; then
    echo "SKIP: Claude Code requires Node.js, but the Node tarball was missing."
else
    echo "[3/3] Installing Claude Code CLI into $CLAUDE_DIST_DIR ..."
    rm -rf "$CLAUDE_DIST_DIR"
    mkdir -p "$CLAUDE_DIST_DIR/wrapper"
    mkdir -p "$CLAUDE_DIST_DIR/native"

    # The npm tarballs contain a top-level "package/" directory; strip it.
    tar -xzf "$WRAPPER_TGZ" -C "$CLAUDE_DIST_DIR/wrapper" --strip-components=1
    tar -xzf "$NATIVE_TGZ"  -C "$CLAUDE_DIST_DIR/native"  --strip-components=1

    # The wrapper's postinstall script links the platform binary over a stub at
    # bin/claude.exe. We do that manually here, hard-link if possible (same FS),
    # otherwise copy.
    # Native package layout: package/claude (binary) at top level after strip-components.
    NATIVE_BIN="$CLAUDE_DIST_DIR/native/claude"
    [ -x "$NATIVE_BIN" ] || NATIVE_BIN="$CLAUDE_DIST_DIR/native/bin/claude"
    WRAPPER_STUB="$CLAUDE_DIST_DIR/wrapper/bin/claude.exe"
    if [ ! -x "$NATIVE_BIN" ]; then
        echo "ERROR: native binary not found at $NATIVE_BIN"
        exit 1
    fi
    rm -f "$WRAPPER_STUB"
    if ln "$NATIVE_BIN" "$WRAPPER_STUB" 2>/dev/null; then
        echo "      hard-linked native binary into wrapper"
    else
        cp "$NATIVE_BIN" "$WRAPPER_STUB"
        echo "      copied native binary into wrapper"
    fi
    chmod +x "$WRAPPER_STUB" "$NATIVE_BIN"

    # Symlink final entrypoint at vendor/claude-dist/bin/claude for convenience.
    mkdir -p "$CLAUDE_DIST_DIR/bin"
    ln -sf "../wrapper/bin/claude.exe" "$CLAUDE_DIST_DIR/bin/claude"

    echo "      Claude CLI installed at $CLAUDE_DIST_DIR/bin/claude"
    echo ""
fi

# ---------------------------------------------------------------------------
echo "Done."
echo ""
echo "Verify:"
echo "  bash $SCRIPT_DIR/../bin/daisy-doctor"
