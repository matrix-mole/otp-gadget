#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MPREMOTE="$SCRIPT_DIR/venv/bin/mpremote"

# ── parse args ────────────────────────────────────────────────────────────────
CLEAN=0
TARGET_LABEL=""
ALL=0
REFRESH_LIST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --clean) CLEAN=1; shift ;;
        --all) ALL=1; shift ;;
        --refresh-list) REFRESH_LIST=1; shift ;;
        --label)
            if [ $# -lt 2 ]; then echo "Error: --label requires a value"; exit 1; fi
            TARGET_LABEL="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; echo "Usage: $0 [--clean] [--refresh-list] [--label <name> | --all]"; exit 1 ;;
    esac
done

if [ -n "$TARGET_LABEL" ] && [ "$ALL" -eq 1 ]; then
    echo "Error: --label and --all are mutually exclusive."
    exit 1
fi

# ── listing cache ─────────────────────────────────────────────────────────────
# Probing a board with mpremote interrupts whatever firmware it's running
# (Ctrl-C → raw REPL → soft reset). To avoid disturbing idle boards on every
# flash, label/version/timestamp are cached per serial port. Pass
# --refresh-list to bypass and re-probe everything.
CACHE_DIR="$HOME/.cache/otp-gadget-flash"
CACHE_FILE="$CACHE_DIR/board-cache.json"
mkdir -p "$CACHE_DIR"
if [ ! -f "$CACHE_FILE" ] || ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CACHE_FILE" 2>/dev/null; then
    echo '{}' > "$CACHE_FILE"
fi

cache_get() {
    # Args: PORT. Prints "label|version|flashed_at" or empty if not cached.
    python3 -c "
import json, sys
cache = json.load(open(sys.argv[1]))
e = cache.get(sys.argv[2])
if e:
    print((e.get('label','') or '') + '|' + (e.get('version','') or '') + '|' + (e.get('flashed_at','') or ''))
" "$CACHE_FILE" "$1"
}

cache_set() {
    # Args: PORT LABEL VERSION FLASHED_AT.
    python3 -c "
import json, sys
try:
    cache = json.load(open(sys.argv[1]))
except Exception:
    cache = {}
cache[sys.argv[2]] = {'label': sys.argv[3], 'version': sys.argv[4], 'flashed_at': sys.argv[5]}
with open(sys.argv[1], 'w') as f:
    json.dump(cache, f, indent=2)
" "$CACHE_FILE" "$1" "$2" "$3" "$4"
}

# ── mpremote retry wrapper ────────────────────────────────────────────────────
# Transient USB CDC errors ("Device not configured", "failed to access ... it
# may be in use by another program") occasionally hit mid-upload on macOS -
# usually right after another board on the same hub was reset. Retry the whole
# invocation a few times with backoff so the OS has time to re-enumerate the
# device and release the previous file descriptor.
mpremote_with_retry() {
    local attempts=3
    local wait_secs=2
    local i=1
    local rc=0
    while :; do
        rc=0
        "$MPREMOTE" "$@" || rc=$?
        if [ "$rc" -eq 0 ]; then return 0; fi
        if [ "$i" -ge "$attempts" ]; then
            echo ""
            echo "  mpremote failed after $attempts attempts (exit $rc)."
            return "$rc"
        fi
        echo ""
        echo "  Transient mpremote error (exit $rc) - retrying in ${wait_secs}s (attempt $((i+1))/$attempts)..."
        sleep "$wait_secs"
        wait_secs=$((wait_secs * 2))
        i=$((i + 1))
    done
}

# ── prereqs ───────────────────────────────────────────────────────────────────
if [ ! -x "$MPREMOTE" ]; then
    echo "Error: mpremote not found at $MPREMOTE"
    echo "Run: cd scripts && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── compute local firmware manifest ──────────────────────────────────────────
cd "$REPO_ROOT"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Compute per-file SHA-256 hashes and an aggregate version hash for all files
# that will be deployed. Non-empty .py files are copied via fs cp; 0-byte
# __init__.py package markers are created via exec touch on the board.
REPO_ROOT="$REPO_ROOT" python3 >"$TMP_DIR/local.json" <<'PYEOF'
import hashlib, json, os, subprocess
from pathlib import Path

os.chdir(os.environ['REPO_ROOT'])

result = subprocess.run(
    ['find', 'firmware', '-type', 'f', '-name', '*.py',
     '-not', '-name', 'sim.py',
     '-not', '-path', 'firmware/sim/*',
     '-not', '-path', 'firmware/tests/*',
     '-not', '-path', 'firmware/setup/*',
     '-not', '-path', '*/__pycache__/*',
     '-not', '-path', 'firmware/venv/*'],
    capture_output=True, text=True,
)
fw_files = sorted(
    f for f in result.stdout.strip().split('\n')
    if f and Path(f).stat().st_size > 0
)

copied = ['main.py'] + fw_files

init_files = [
    'firmware/__init__.py', 'firmware/core/__init__.py',
    'firmware/core/screens/__init__.py', 'firmware/core/crypto/__init__.py',
    'firmware/core/fonts/__init__.py', 'firmware/core/widgets/__init__.py',
    'firmware/core/vendor/__init__.py', 'firmware/hal/__init__.py',
    'firmware/hal/drivers/__init__.py',
]
copied_set = set(copied)
init_files = [f for f in init_files if f not in copied_set]

EMPTY_HASH = hashlib.sha256(b'').hexdigest()
file_hashes = {}
for f in copied:
    file_hashes[f] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
for f in init_files:
    file_hashes[f] = EMPTY_HASH

lines = ''.join(f'{k} {v}\n' for k, v in sorted(file_hashes.items()))
version = hashlib.sha256(lines.encode()).hexdigest()[:7]

print(json.dumps({
    'version': version,
    'files': file_hashes,
    '_copied': copied,
    '_init': init_files,
}))
PYEOF

LOCAL_VERSION=$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1]))['version'])
" "$TMP_DIR/local.json")

# ── discover boards ───────────────────────────────────────────────────────────
echo "Scanning for connected boards..."
PORTS=()
while IFS= read -r line; do
    [[ "$line" =~ ^/dev/ ]] || [[ "$line" =~ ^COM ]] || continue
    # Skip macOS virtual ports (Bluetooth, debug-console)
    [[ "$line" =~ Bluetooth|debug-console ]] && continue
    PORTS+=("${line%% *}")
done < <("$MPREMOTE" devs 2>/dev/null || true)

if [ ${#PORTS[@]} -eq 0 ]; then
    echo "No boards found. Connect a board and try again."
    exit 1
fi

# ── read labels and manifests from each board ─────────────────────────────────
# Cache-first: skip probing a port whose label/version/timestamp we already
# know from a previous run. Probing soft-resets the running firmware, so we
# only do it for unknown ports (or when --refresh-list is given). A single
# exec per probe reads both the device label and the firmware manifest.
LABELS=()
BOARD_VERSIONS=()
BOARD_TIMESTAMPS=()
for PORT in "${PORTS[@]}"; do
    HIT=""
    if [ "$REFRESH_LIST" -eq 0 ]; then
        HIT=$(cache_get "$PORT")
    fi
    if [ -n "$HIT" ]; then
        IFS='|' read -r lbl ver ts <<< "$HIT"
    else
        INFO=$("$MPREMOTE" connect "$PORT" exec "
import json
try:
    label = open('/flash/device_label.txt').read().strip()
except:
    label = ''
try:
    m = json.load(open('/flash/firmware_manifest.json'))
    ver = m.get('version', '')
    ts = m.get('flashed_at', '')
except:
    ver = ''
    ts = ''
print(label + '|' + ver + '|' + ts)
" 2>/dev/null | tr -d '\r' | tail -1 | tr -d '\n' || true)
        INFO="${INFO}||"
        IFS='|' read -r lbl ver ts dummy <<< "$INFO"
        # Persist probe result so future runs don't re-probe this port.
        cache_set "$PORT" "${lbl:-}" "${ver:-}" "${ts:-}"
    fi
    LABELS+=("${lbl:-[unlabeled]}")
    BOARD_VERSIONS+=("${ver:-}")
    BOARD_TIMESTAMPS+=("${ts:-}")
done

# ── pick board(s) ─────────────────────────────────────────────────────────────
echo ""
echo "Local firmware: $LOCAL_VERSION"
echo ""

INDICES=()
if [ -n "$TARGET_LABEL" ]; then
    # Non-interactive: find the board whose label matches TARGET_LABEL.
    IDX=-1
    for i in "${!PORTS[@]}"; do
        if [ "${LABELS[$i]}" = "$TARGET_LABEL" ]; then
            IDX=$i
            break
        fi
    done
    if [ "$IDX" -eq -1 ]; then
        echo "Error: no connected board labeled '$TARGET_LABEL'."
        echo "Connected boards:"
        for i in "${!PORTS[@]}"; do
            printf "  %-20s  %s\n" "${LABELS[$i]}" "${PORTS[$i]}"
        done
        echo ""
        echo "If this is a new board, run without --label to assign it a label first."
        exit 1
    fi
    INDICES=("$IDX")
    echo "Targeting ${LABELS[$IDX]} (${PORTS[$IDX]})"
elif [ "$ALL" -eq 1 ]; then
    # Non-interactive --all: error if any board is unlabeled.
    for i in "${!PORTS[@]}"; do
        if [ "${LABELS[$i]}" = "[unlabeled]" ]; then
            echo "Error: board ${PORTS[$i]} has no label."
            echo "Run interactively (without --all/--label) to assign a label first."
            exit 1
        fi
    done
    for i in "${!PORTS[@]}"; do INDICES+=("$i"); done
    echo "Targeting all ${#PORTS[@]} connected board(s):"
    for i in "${INDICES[@]}"; do
        printf "  - %-20s  %s\n" "${LABELS[$i]}" "${PORTS[$i]}"
    done
else
    echo "Connected boards:"
    for i in "${!PORTS[@]}"; do
        ver="${BOARD_VERSIONS[$i]}"
        ts="${BOARD_TIMESTAMPS[$i]}"
        if [ -z "$ver" ]; then
            status_str="(no manifest)"
        elif [ "$ver" = "$LOCAL_VERSION" ]; then
            status_str="$ver  ✓  $ts"
        else
            status_str="$ver  ⚠  $ts  (outdated)"
        fi
        printf "  %d) %-20s  %-32s  %s\n" "$((i+1))" "${LABELS[$i]}" "${PORTS[$i]}" "$status_str"
    done
    echo ""

    while true; do
        read -rp "Select board [1-${#PORTS[@]}, or 'a' for all]: " CHOICE
        if [[ "$CHOICE" =~ ^[Aa]$ ]]; then
            for i in "${!PORTS[@]}"; do INDICES+=("$i"); done
            break
        elif [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -ge 1 ] && [ "$CHOICE" -le "${#PORTS[@]}" ]; then
            INDICES=("$((CHOICE - 1))")
            break
        fi
        echo "Please enter a number between 1 and ${#PORTS[@]}, or 'a' for all."
    done
fi

# ── per-board flash routine ───────────────────────────────────────────────────
# Wraps everything from labelling through reset, so we can call it once per
# selected board. Uses script-globals: MPREMOTE, REPO_ROOT, TMP_DIR, CLEAN,
# LOCAL_VERSION, PORTS, LABELS.
flash_one() {
    local IDX="$1"
    PORT="${PORTS[$IDX]}"
    LABEL="${LABELS[$IDX]}"

# ── label unlabeled board ─────────────────────────────────────────────────────
if [ "$LABEL" = "[unlabeled]" ] && [ -n "$TARGET_LABEL" ]; then
    echo "Error: matching board has no label yet. Use interactive mode to assign one first."
    exit 1
fi

if [ "$LABEL" = "[unlabeled]" ]; then
    echo ""
    while true; do
        read -rp "Label for this board (e.g. alice, bob): " NEW_LABEL
        NEW_LABEL=$(echo "$NEW_LABEL" | tr -d '[:space:]')
        [ -n "$NEW_LABEL" ] && break
        echo "Label cannot be empty."
    done
    # Ensure /flash/ dir exists on the board (created by firmware on first run,
    # but may not exist yet on a freshly flashed MicroPython board).
    "$MPREMOTE" connect "$PORT" exec "
import os
try:
    os.mkdir('/flash')
except OSError:
    pass
" 2>/dev/null || true
    TMP_LABEL=$(mktemp)
    printf '%s' "$NEW_LABEL" > "$TMP_LABEL"
    mpremote_with_retry connect "$PORT" fs cp "$TMP_LABEL" :/flash/device_label.txt
    rm -f "$TMP_LABEL"
    LABEL="$NEW_LABEL"
    echo "Label saved: $LABEL"
fi

# ── load full file lists from local manifest ──────────────────────────────────
FILES_TO_CP=()
while IFS= read -r f; do
    [ -n "$f" ] && FILES_TO_CP+=("$f")
done < <(python3 -c "
import json, sys
for f in json.load(open(sys.argv[1]))['_copied']:
    print(f)
" "$TMP_DIR/local.json")

INITS_TO_TOUCH=()
while IFS= read -r f; do
    [ -n "$f" ] && INITS_TO_TOUCH+=("$f")
done < <(python3 -c "
import json, sys
for f in json.load(open(sys.argv[1]))['_init']:
    print(f)
" "$TMP_DIR/local.json")

FILES_TO_DELETE=()

# ── wipe (clean mode only) ────────────────────────────────────────────────────
echo ""
if [ "$CLEAN" -eq 1 ]; then
    echo "Wiping /firmware/ and /main.py on $LABEL ($PORT)... (/flash/ preserved)"
    "$MPREMOTE" connect "$PORT" exec "
import os
CLEAN = $CLEAN
def rm_tree(p):
    try: entries = os.listdir(p)
    except OSError: return
    for name in entries:
        full = p + '/' + name if p != '/' else '/' + name
        try:
            os.remove(full)
        except OSError:
            rm_tree(full)
            try: os.rmdir(full)
            except OSError: pass
def rm_dir(p):
    rm_tree(p)
    try: os.rmdir(p)
    except OSError: pass
def rm_file(p):
    try: os.remove(p)
    except OSError: pass

rm_dir('/firmware')
rm_file('/main.py')

try: os.mkdir('/firmware')
except OSError: pass
try: os.mkdir('/firmware/hal')
except OSError: pass
"
    echo ""
fi

# ── incremental diff (normal mode only) ───────────────────────────────────────
echo "Flashing $LABEL ($PORT)..."

if [ "$CLEAN" -eq 0 ]; then
    # Fetch the board's current file manifest; fall back to empty if missing/corrupt.
    "$MPREMOTE" connect "$PORT" exec "
import json
try:
    m = json.load(open('/flash/firmware_manifest.json'))
    print(json.dumps(m.get('files', {})))
except:
    print('{}')
" 2>/dev/null | tr -d '\r' | tail -1 > "$TMP_DIR/remote.json" || true
    # Validate JSON; fall back to empty manifest if the output was corrupt/empty.
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$TMP_DIR/remote.json" 2>/dev/null \
        || echo '{}' > "$TMP_DIR/remote.json"

    # Compute which files need uploading / deleting.
    python3 -c "
import json, sys
local  = json.load(open(sys.argv[1]))
remote = json.load(open(sys.argv[2]))

init_set   = set(local['_init'])
local_files = local['files']

cp     = []
touch  = []
delete = []

for path, lhash in local_files.items():
    if path not in remote or remote[path] != lhash:
        (touch if path in init_set else cp).append(path)

for path in remote:
    if path not in local_files:
        delete.append(path)

print(json.dumps({'cp': cp, 'touch': touch, 'delete': delete}))
" "$TMP_DIR/local.json" "$TMP_DIR/remote.json" > "$TMP_DIR/diff.json"

    FILES_TO_CP=()
    while IFS= read -r f; do
        [ -n "$f" ] && FILES_TO_CP+=("$f")
    done < <(python3 -c "
import json, sys
for f in json.load(open(sys.argv[1]))['cp']:
    print(f)
" "$TMP_DIR/diff.json")

    INITS_TO_TOUCH=()
    while IFS= read -r f; do
        [ -n "$f" ] && INITS_TO_TOUCH+=("$f")
    done < <(python3 -c "
import json, sys
for f in json.load(open(sys.argv[1]))['touch']:
    print(f)
" "$TMP_DIR/diff.json")

    FILES_TO_DELETE=()
    while IFS= read -r f; do
        [ -n "$f" ] && FILES_TO_DELETE+=("$f")
    done < <(python3 -c "
import json, sys
for f in json.load(open(sys.argv[1]))['delete']:
    print(f)
" "$TMP_DIR/diff.json")

    TOTAL_CHANGES=$(( ${#FILES_TO_CP[@]} + ${#INITS_TO_TOUCH[@]} + ${#FILES_TO_DELETE[@]} ))

    if [ "$TOTAL_CHANGES" -eq 0 ]; then
        echo "  $LABEL is already up to date — nothing to do."
        cache_set "$PORT" "$LABEL" "$LOCAL_VERSION" "${BOARD_TIMESTAMPS[$IDX]}"
        echo ""
        echo "Resetting board..."
        "$MPREMOTE" connect "$PORT" reset
        echo ""
        echo "Done - $LABEL already up to date."
        return 0
    fi

    echo "  ${#FILES_TO_CP[@]} file(s) to upload, ${#FILES_TO_DELETE[@]} to delete."

    # Delete files that were removed from source.
    if [ "${#FILES_TO_DELETE[@]}" -gt 0 ]; then
        DELETE_LIST=$(python3 -c "
import json, sys
paths = json.load(open(sys.argv[1]))['delete']
print('[' + ', '.join(repr('/' + p) for p in paths) + ']')
" "$TMP_DIR/diff.json")
        "$MPREMOTE" connect "$PORT" exec "
import os
for p in ${DELETE_LIST}:
    try: os.remove(p)
    except OSError: pass
"
    fi
fi

# ── upload files ──────────────────────────────────────────────────────────────
# Board layout mirrors the repo:
#   /main.py            ← from repo root main.py
#   /firmware/          ← from firmware/ (selective, see below)
#     __init__.py
#     core/
#     hal/
#       __init__.py
#       base.py
#       real.py
#       drivers/
#
# Excluded: hal/sim.py, sim/, tests/, setup/, README.md files
#
# Why individual `fs cp` per file (no `cp -r`): mpremote's recursive copy is
# unreliable - it nests when the destination exists and inconsistently rejects
# the -r flag at deeper destination paths. Per-file copies are robust and
# batched into one mpremote connection via `+` so it's still fast.

TOTAL_UPLOAD=$(( ${#FILES_TO_CP[@]} + ${#INITS_TO_TOUCH[@]} ))

if [ "$TOTAL_UPLOAD" -gt 0 ]; then
    echo "  uploading $TOTAL_UPLOAD file(s)..."

    MKDIR_EXEC="
import os
def mkdirs(p):
    parts = [x for x in p.split('/') if x]
    for i in range(1, len(parts) + 1):
        try: os.mkdir('/' + '/'.join(parts[:i]))
        except OSError: pass
for d in ['firmware/core/screens', 'firmware/core/crypto', 'firmware/core/fonts',
         'firmware/core/widgets', 'firmware/core/vendor', 'firmware/hal/drivers']:
    mkdirs(d)
"

    ARGS=(connect "$PORT" exec "$MKDIR_EXEC")

    if [ "${#FILES_TO_CP[@]}" -gt 0 ]; then
        for f in "${FILES_TO_CP[@]}"; do
            ARGS+=(+ fs cp "$f" ":$f")
        done
    fi

    # 0-byte __init__.py files cannot be copied via mpremote fs cp - touch them.
    if [ "${#INITS_TO_TOUCH[@]}" -gt 0 ]; then
        INIT_PY_LIST=$(python3 -c "
import sys
paths = sys.argv[1:]
print('[' + ', '.join(repr('/' + p) for p in paths) + ']')
" "${INITS_TO_TOUCH[@]}")
        INIT_EXEC="
for p in ${INIT_PY_LIST}:
    open(p, 'w').close()
"
        ARGS+=(+ exec "$INIT_EXEC")
    fi

    mpremote_with_retry "${ARGS[@]}"
fi

# ── write manifest to board ───────────────────────────────────────────────────
# Written last so a crashed upload never leaves the board with a stale manifest
# that would cause the next incremental flash to skip files that didn't copy.
FLASHED_AT=$(date -u '+%Y-%m-%d %H:%M UTC')
python3 -c "
import json, sys
m = json.load(open(sys.argv[1]))
out = {'version': m['version'], 'flashed_at': sys.argv[2], 'files': m['files']}
print(json.dumps(out, indent=2))
" "$TMP_DIR/local.json" "$FLASHED_AT" > "$TMP_DIR/board_manifest.json"

mpremote_with_retry connect "$PORT" fs cp "$TMP_DIR/board_manifest.json" :/flash/firmware_manifest.json

# ── update listing cache so next run doesn't re-probe this port ──────────────
cache_set "$PORT" "$LABEL" "$LOCAL_VERSION" "$FLASHED_AT"

# ── reset ─────────────────────────────────────────────────────────────────────
echo ""
echo "Resetting board..."
"$MPREMOTE" connect "$PORT" reset

echo ""
echo "Done - $LABEL flashed."
}

# ── flash each selected board ─────────────────────────────────────────────────
# When flashing multiple boards, give the USB bus a few seconds to settle
# between resets - on macOS the previous board's reset can briefly perturb the
# next board's CDC endpoint and a follow-up mpremote call then fails with
# "Device not configured" (sometimes mid-upload, after the connection has
# already been opened). 5 s is empirically enough on a busy hub; the
# mpremote_with_retry wrapper above handles the rare cases where it isn't.
N_BOARDS=${#INDICES[@]}
for k in "${!INDICES[@]}"; do
    if [ "$N_BOARDS" -gt 1 ]; then
        if [ "$k" -gt 0 ]; then
            echo ""
            echo "Waiting 5s for USB to settle..."
            sleep 5
        fi
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo "  Board $((k + 1)) of $N_BOARDS"
        echo "════════════════════════════════════════════════════════════════"
    fi
    flash_one "${INDICES[$k]}"
done
