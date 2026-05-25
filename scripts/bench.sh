#!/usr/bin/env bash
# bench.sh — run the exchange speed benchmark on a connected OTP gadget board.
#
# Usage:
#   ./scripts/bench.sh                  # auto-selects if one board is connected
#   ./scripts/bench.sh --label alice    # target a specific board by label
#
# The board's running firmware is interrupted for the duration of the benchmark.
# Restart it afterwards with:  ./scripts/reset.sh --label <name>
#
# Results are streamed to stdout and also saved to:
#   firmware/setup/bench_results_YYYYMMDD_HHMMSS.txt
# so you can compare before/after when implementing fixes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MPREMOTE="$SCRIPT_DIR/venv/bin/mpremote"
BENCH_PY="$REPO_ROOT/firmware/setup/bench_exchange.py"

# ── parse args ────────────────────────────────────────────────────────────────

TARGET_LABEL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --label)
            if [ $# -lt 2 ]; then echo "Error: --label requires a value"; exit 1; fi
            TARGET_LABEL="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,12p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--label <name>]"
            exit 1 ;;
    esac
done

# ── prereqs ───────────────────────────────────────────────────────────────────

if [ ! -x "$MPREMOTE" ]; then
    echo "Error: mpremote not found at $MPREMOTE"
    echo "Run: cd scripts && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "$BENCH_PY" ]; then
    echo "Error: benchmark script not found at $BENCH_PY"
    exit 1
fi

# ── discover boards ───────────────────────────────────────────────────────────

PORTS=()
while IFS= read -r line; do
    [[ "$line" =~ ^/dev/ ]] || [[ "$line" =~ ^COM ]] || continue
    [[ "$line" =~ Bluetooth|debug-console ]] && continue
    PORTS+=("${line%% *}")
done < <("$MPREMOTE" devs 2>/dev/null || true)

if [ ${#PORTS[@]} -eq 0 ]; then
    echo "No boards found. Connect a board via USB-C and try again."
    exit 1
fi

# Read label from a port (interrupts running firmware briefly).
_read_label() {
    "$MPREMOTE" connect "$1" exec \
"try:
    print(open('/flash/device_label.txt').read().strip())
except:
    print('')
" 2>/dev/null | tr -d '\r' | tail -1 | tr -d '\n' || true
}

# ── select board ──────────────────────────────────────────────────────────────

PORT=""
LABEL=""

if [ -n "$TARGET_LABEL" ]; then
    # Non-interactive: match by label.
    for p in "${PORTS[@]}"; do
        lbl=$(_read_label "$p")
        if [ "$lbl" = "$TARGET_LABEL" ]; then
            PORT="$p"
            LABEL="$lbl"
            break
        fi
    done
    if [ -z "$PORT" ]; then
        echo "Error: no connected board labeled '$TARGET_LABEL'."
        echo ""
        echo "Connected boards:"
        for p in "${PORTS[@]}"; do
            lbl=$(_read_label "$p")
            printf "  %-20s  %s\n" "${lbl:-[unlabeled]}" "$p"
        done
        exit 1
    fi

elif [ ${#PORTS[@]} -eq 1 ]; then
    # Single board – auto-select.
    PORT="${PORTS[0]}"
    LABEL=$(_read_label "$PORT")
    LABEL="${LABEL:-[unlabeled]}"

else
    # Multiple boards – prompt.
    echo "Multiple boards connected:"
    declare -a _PORTS _LABELS
    for i in "${!PORTS[@]}"; do
        lbl=$(_read_label "${PORTS[$i]}")
        _PORTS+=("${PORTS[$i]}")
        _LABELS+=("${lbl:-[unlabeled]}")
        printf "  %d) %-20s  %s\n" "$((i+1))" "${lbl:-[unlabeled]}" "${PORTS[$i]}"
    done
    echo ""
    while true; do
        read -rp "Select board [1-${#PORTS[@]}]: " CHOICE
        if [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -ge 1 ] && [ "$CHOICE" -le "${#PORTS[@]}" ]; then
            IDX=$((CHOICE - 1))
            PORT="${_PORTS[$IDX]}"
            LABEL="${_LABELS[$IDX]}"
            break
        fi
        echo "Please enter a number between 1 and ${#PORTS[@]}."
    done
fi

# ── run benchmark ─────────────────────────────────────────────────────────────

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOGFILE="$REPO_ROOT/firmware/setup/bench_results_${TIMESTAMP}.txt"

echo ""
echo "Board:    $LABEL  ($PORT)"
echo "Script:   firmware/setup/bench_exchange.py"
echo "Log:      firmware/setup/bench_results_${TIMESTAMP}.txt"
echo ""
echo "Make sure the own SD card is inserted (onboard TF slot / left slot in the case)."
echo "Running benchmark — this takes several minutes..."
echo ""

# Interrupt running firmware and execute the benchmark script.
# mpremote sends Ctrl-C automatically to stop main.py before running the script.
# Output is streamed live to the terminal AND captured to the log file.
{
    echo "Board: $LABEL  ($PORT)"
    echo "Date:  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo ""
} | tee "$LOGFILE"

RC=0
"$MPREMOTE" connect "$PORT" run "$BENCH_PY" 2>&1 | tee -a "$LOGFILE" || RC=$?

echo ""
if [ "$RC" -eq 0 ]; then
    echo "Results saved to: firmware/setup/bench_results_${TIMESTAMP}.txt"
    echo ""
    echo "To restart the firmware:  ./scripts/reset.sh --label $LABEL"
else
    echo "Benchmark exited with code $RC  (check output above for errors)."
    echo "Partial results saved to: firmware/setup/bench_results_${TIMESTAMP}.txt"
    echo ""
    echo "To restart the firmware:  ./scripts/reset.sh --label $LABEL"
    exit "$RC"
fi
