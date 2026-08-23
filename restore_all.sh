#!/bin/bash

# SmartPanel Snapshot - Restore All
#
# Restores every SmartPanel snapshot found in the local snapshots directory.
#
# Optional:
#
#   ./restore_all.sh --normalize
#
# The --normalize option is passed to restore.py for every panel.
#
# IMPORTANT:
# Restore actively changes SmartPanel state.

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
SNAPSHOT_DIR="$PROJECT_DIR/snapshots"

NORMALIZE=""

if [[ "${1:-}" == "--normalize" ]]; then
    NORMALIZE="--normalize"
fi


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Find snapshots
# ---------------------------------------------------------------------------

shopt -s nullglob

snapshots=("$SNAPSHOT_DIR"/*.json)

if [ ${#snapshots[@]} -eq 0 ]; then
    echo "No SmartPanel snapshots found."
    exit 0
fi

echo "Restoring ${#snapshots[@]} SmartPanel snapshot(s)..."

if [ -n "$NORMALIZE" ]; then
    echo "NSA normalization: ENABLED"
else
    echo "NSA normalization: disabled"
fi

echo


# ---------------------------------------------------------------------------
# Restore panels
# ---------------------------------------------------------------------------
#
# Restore is intentionally sequential for now.
#
# Unlike compare/check operations, restore actively modifies panel state.
# Sequential execution makes the process easier to follow, troubleshoot,
# and stop if an unexpected condition is encountered.
#

failed=0

for snapshot in "${snapshots[@]}"; do

    echo "=================================================="
    echo "[RESTORE] $(basename "$snapshot")"
    echo "=================================================="

    if [ -n "$NORMALIZE" ]; then

        if ! "$PYTHON" "$PROJECT_DIR/restore.py" "$snapshot" --normalize; then
            failed=$((failed + 1))
        fi

    else

        if ! "$PYTHON" "$PROJECT_DIR/restore.py" "$snapshot"; then
            failed=$((failed + 1))
        fi

    fi

    echo

done


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

echo "Restore-all complete."

if [ "$failed" -gt 0 ]; then
    echo "WARNING: $failed restore operation(s) failed."
    exit 1
fi

exit 0
