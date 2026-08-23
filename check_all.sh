#!/bin/bash

# SmartPanel Snapshot - Check All
#
# Runs compare.py against every snapshot in the local snapshots directory.
#
# Each comparison runs in parallel so multiple SmartPanels do not need to
# wait for each other.
#
# This script expects the project-local Python virtual environment:
#
#   .venv/
#
# Generated comparison metrics are written by compare.py.

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
SNAPSHOT_DIR="$PROJECT_DIR/snapshots"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Python virtual environment not found:"
    echo "$PYTHON"
    exit 1
fi

if [ ! -d "$SNAPSHOT_DIR" ]; then
    echo "ERROR: Snapshot directory not found:"
    echo "$SNAPSHOT_DIR"
    exit 1
fi

shopt -s nullglob

snapshots=("$SNAPSHOT_DIR"/*.json)

if [ ${#snapshots[@]} -eq 0 ]; then
    echo "No SmartPanel snapshots found."
    exit 0
fi

echo "Checking ${#snapshots[@]} SmartPanel snapshot(s)..."
echo

pids=()

for snapshot in "${snapshots[@]}"; do

    echo "[CHECK] $(basename "$snapshot")"

    "$PYTHON" "$PROJECT_DIR/compare.py" "$snapshot" &

    pids+=("$!")

done

failed=0

for pid in "${pids[@]}"; do

    if ! wait "$pid"; then
        failed=$((failed + 1))
    fi

done

echo
echo "Check complete."

if [ "$failed" -gt 0 ]; then
    echo "WARNING: $failed comparison process(es) failed."
    exit 1
fi

exit 0
