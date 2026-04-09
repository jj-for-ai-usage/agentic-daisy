#!/bin/bash
# Unpack vendored wheel files into vendor/lib/ for offline use.
#
# Usage (one-time, after git clone/pull):
#   bash vendor/install.sh
#
# This extracts all .whl files into vendor/lib/ so they can be
# found by Python via PYTHONPATH.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEELS_DIR="$SCRIPT_DIR/wheels"
LIB_DIR="$SCRIPT_DIR/lib"

if [ ! -d "$WHEELS_DIR" ] || [ -z "$(ls "$WHEELS_DIR"/*.whl 2>/dev/null)" ]; then
    echo "ERROR: No wheel files found in $WHEELS_DIR"
    exit 1
fi

echo "Unpacking vendored wheels into $LIB_DIR ..."
# Clean out stale contents so wheels deleted from vendor/wheels/ do not
# linger in vendor/lib/ (e.g. the removed rich/markdown_it/mdurl/pygments).
rm -rf "$LIB_DIR"
mkdir -p "$LIB_DIR"

count=0
for whl in "$WHEELS_DIR"/*.whl; do
    name="$(basename "$whl")"
    unzip -o -q "$whl" -d "$LIB_DIR"
    count=$((count + 1))
    echo "  [$count] $name"
done

echo ""
echo "Done. Unpacked $count wheels into $LIB_DIR"
echo ""
echo "Verify with:"
echo "  PYTHONPATH=$LIB_DIR python3 -c \"import anthropic; print('OK')\""
