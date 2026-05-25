#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d venv ]; then
    python3 -m venv venv
fi

venv/bin/pip install --quiet -r requirements.txt

venv/bin/python qr_size_check.py
