#!/bin/bash

# SmartPanel Snapshot - Save All
#
# Wrapper used by the REST API and manual operation.
#
# The SmartPanel network and scan settings are loaded by save.py
# from config.yaml.

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Python virtual environment not found:"
    echo "$PYTHON"
    exit 1
fi

"$PYTHON" "$PROJECT_DIR/save.py"

exit $?
