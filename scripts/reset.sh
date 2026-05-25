#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MPREMOTE="$SCRIPT_DIR/venv/bin/mpremote"

TARGET_LABEL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --label)
            if [ $# -lt 2 ]; then echo "Error: --label requires a value"; exit 1; fi
            TARGET_LABEL="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; echo "Usage: $0 [--label <name>]"; exit 1 ;;
    esac
done

if [ ! -x "$MPREMOTE" ]; then
    echo "Error: mpremote not found at $MPREMOTE"
    echo "Run: cd scripts && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

echo "Scanning for connected boards..."
PORTS=()
while IFS= read -r line; do
    [[ "$line" =~ ^/dev/ ]] || [[ "$line" =~ ^COM ]] || continue
    [[ "$line" =~ Bluetooth|debug-console ]] && continue
    PORTS+=("${line%% *}")
done < <("$MPREMOTE" devs 2>/dev/null || true)

if [ ${#PORTS[@]} -eq 0 ]; then
    echo "No boards found. Connect a board and try again."
    exit 1
fi

LABELS=()
for PORT in "${PORTS[@]}"; do
    INFO=$("$MPREMOTE" connect "$PORT" exec "
try:
    label = open('/flash/device_label.txt').read().strip()
except:
    label = ''
print(label)
" 2>/dev/null | tr -d '\r' | tail -1 | tr -d '\n' || true)
    LABELS+=("${INFO:-[unlabeled]}")
done

RESET_COUNT=0
for i in "${!PORTS[@]}"; do
    PORT="${PORTS[$i]}"
    LABEL="${LABELS[$i]}"
    if [ -n "$TARGET_LABEL" ] && [ "$LABEL" != "$TARGET_LABEL" ]; then
        continue
    fi
    echo "Resetting $LABEL ($PORT)..."
    "$MPREMOTE" connect "$PORT" reset
    RESET_COUNT=$(( RESET_COUNT + 1 ))
done

if [ "$RESET_COUNT" -eq 0 ]; then
    echo "Error: no connected board labeled '$TARGET_LABEL'."
    echo "Connected boards:"
    for i in "${!PORTS[@]}"; do
        printf "  %-20s  %s\n" "${LABELS[$i]}" "${PORTS[$i]}"
    done
    exit 1
fi

echo ""
echo "Done - $RESET_COUNT board(s) reset."
