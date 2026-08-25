#!/bin/bash

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Python virtual environment not found:"
    echo "$PYTHON"
    exit 1
fi

NORMALIZE=""

if [[ "$1" == "--normalize" ]]; then
    NORMALIZE="--normalize"
fi

cd "$PROJECT_DIR"

mkdir -p logs

echo
echo "Restoring SmartPanels in parallel..."
echo "Normalization: ${NORMALIZE:-disabled}"
echo

pids=()
names=()

for snapshot in snapshots/*.json; do
    [[ -e "$snapshot" ]] || continue

    name=$(basename "$snapshot" .json)

    echo "[START] $name"

    "$PYTHON" "$PROJECT_DIR/restore.py" "$snapshot" $NORMALIZE \
        > "logs/restore-${name}.log" 2>&1 &

    pids+=("$!")
    names+=("$name")
done

echo
echo "Waiting for restores..."
echo

failed=0

for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    name="${names[$i]}"

    if wait "$pid"; then
        echo "[OK]   $name"
    else
        echo "[FAIL] $name"
        failed=1
    fi
done

echo

if [[ "$failed" -eq 0 ]]; then
    echo "Restore-all complete."
    echo
    echo "Running final compliance check..."

    if "$PROJECT_DIR/check_all.sh"; then
        echo
        echo "Final compliance check complete."
    else
        echo
        echo "WARNING: Restore completed, but final compliance check failed."
        exit 1
    fi
else
    echo "Restore-all completed with failures."
    exit 1
fi
