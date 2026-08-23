#!/bin/bash

set -e

NORMALIZE=""

if [[ "$1" == "--normalize" ]]; then
    NORMALIZE="--normalize"
fi

cd "$(dirname "$0")"

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

    python restore.py "$snapshot" $NORMALIZE \
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
else
    echo "Restore-all completed with failures."
    exit 1
fi
