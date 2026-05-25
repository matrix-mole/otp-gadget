#!/usr/bin/env bash
# inspect.sh — post-freeze debug tool for the OTP gadget.
# Wrapper around inspect.py; all heavy lifting is in Python.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"

if [ ! -x "$PYTHON" ]; then
    echo "Error: venv not found at $SCRIPT_DIR/venv"
    echo "Run: cd scripts && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/inspect.py" "$@"
