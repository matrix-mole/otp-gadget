#!/usr/bin/env python3
"""inspect.py - post-freeze debug tool for the OTP gadget.

Called by inspect.sh. Not meant to be run directly (use the .sh wrapper).

All verbs use mpremote, which briefly interrupts the running firmware via
Ctrl-C/raw-REPL. This is acceptable for debug sessions. The file-based touch
injection queue (/flash/inject_queue.txt) persists across the interruption so
the firmware reads the click on the next get_touch() poll after resuming.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MPREMOTE = os.path.join(SCRIPT_DIR, "venv", "bin", "mpremote")

_EXPECTED_I2C = {0x15: "touch", 0x34: "axp", 0x51: "rtc"}

_RESET_CAUSE_NAMES = {
    1: "PWRON", 2: "HARD_RESET", 3: "WDT", 4: "DEEPSLEEP", 5: "SOFT_RESET",
}

# Key layout maps: char -> (x, y) center coords for synthetic taps.
# Based on keyboard.py / keypad.py geometry (portrait 320x480).
#
# QWERTY keyboard (_KBD_Y=288, row_h=48, key_h=46):
_KB_Y = 288
_ROW_H = 48
_QWERTY_MAP = {}

# Row 0: qwertyuiop - 10 keys, width 30, step 32, starting x=1
for _i, _c in enumerate('qwertyuiop'):
    _QWERTY_MAP[_c] = (1 + _i * 32 + 15, _KB_Y + 23)

# Row 1: asdfghjkl - 9 keys centered; sx = (320 - (9*30 + 8*2)) // 2 = 17
_SX9 = 17
for _i, _c in enumerate('asdfghjkl'):
    _QWERTY_MAP[_c] = (_SX9 + _i * 32 + 15, _KB_Y + _ROW_H + 23)

# Row 2: zxcvbnm - fn2=47 wide shift, then chars, then fn2 back
_FN2 = 47
for _i, _c in enumerate('zxcvbnm'):
    _QWERTY_MAP[_c] = (_FN2 + 2 + _i * 32 + 15, _KB_Y + 2 * _ROW_H + 23)

# Shift key center (row 2, x=0, w=47)
_QWERTY_SHIFT = (23, _KB_Y + 2 * _ROW_H + 23)

# Row 3: 123 (fn3=77), space (162), GO (fn3=77)
_QWERTY_DONE = (77 + 2 + 162 + 2 + 38, _KB_Y + 3 * _ROW_H + 23)   # GO
_QWERTY_SPACE = (77 + 2 + 81, _KB_Y + 3 * _ROW_H + 23)             # space
_QWERTY_NUM = (38, _KB_Y + 3 * _ROW_H + 23)                         # 123 layer switch

# Numbers layer (same y positions as qwerty rows 0-1, different chars)
_NUM_MAP = {}
for _i, _c in enumerate('1234567890'):
    _NUM_MAP[_c] = (1 + _i * 32 + 15, _KB_Y + 23)
for _i, _c in enumerate('-/:;()$&@"'):
    _NUM_MAP[_c] = (_SX9 + _i * 32 + 15, _KB_Y + _ROW_H + 23)

# ABC button (back to letters from numbers layer) - same position as '123' key
_NUM_ABC = (38, _KB_Y + 3 * _ROW_H + 23)

# Digit keypad (PIN entry): _DK_Y = 240 + 48 = 288, _DK_W=104, x=(2,108,214)
_DK_Y = 288
_DK_W = 104
_DK_X = (2, 108, 214)
_DIGIT_MAP = {}
for _r, _row in enumerate(['123', '456', '789']):
    for _ci, _c in enumerate(_row):
        _DIGIT_MAP[_c] = (_DK_X[_ci] + _DK_W // 2, _DK_Y + _r * _ROW_H + 23)
_DIGIT_MAP['0'] = (_DK_X[1] + _DK_W // 2, _DK_Y + 3 * _ROW_H + 23)
_DIGIT_MAP['\x03'] = (_DK_X[2] + _DK_W // 2, _DK_Y + 3 * _ROW_H + 23)  # GO

# Hex keypad: _KBD_Y=240, _HK_W=78, x=(0,80,160,240)
_HK_Y = 240
_HK_W = 78
_HK_X = (0, 80, 160, 240)
_HEX_MAP = {}
for _r, _row in enumerate(['0123', '4567', '89AB', 'CDEF']):
    for _ci, _c in enumerate(_row):
        _HEX_MAP[_c] = (_HK_X[_ci] + _HK_W // 2, _HK_Y + _r * _ROW_H + 23)
_HEX_MAP['\x03'] = (160 + 80, _HK_Y + 4 * _ROW_H + 23)  # GO (x=160, w=160)


def _run(args, capture=True, timeout=10):
    r = subprocess.run(
        [MPREMOTE] + args,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )
    return r


def _scan_ports() -> list[str]:
    r = _run(["devs"])
    ports = []
    for line in r.stdout.splitlines():
        if not (line.startswith("/dev/") or line.startswith("COM")):
            continue
        if "Bluetooth" in line or "debug-console" in line:
            continue
        ports.append(line.split()[0])
    return ports


def _read_board_info(port: str) -> tuple[str, str, str]:
    """Returns (label, version, timestamp) for a board; empty strings on failure."""
    r = _run(["connect", port, "exec", r"""
import json
try: label=open('/flash/device_label.txt').read().strip()
except: label=''
try:
    m=json.load(open('/flash/firmware_manifest.json'))
    ver=m.get('version','')
    ts=m.get('flashed_at','')
except:
    ver=''; ts=''
print(label+'|'+ver+'|'+ts)
"""], timeout=8)
    last = (r.stdout or "").strip().splitlines()[-1] if r.stdout else "||"
    parts = (last + "||").split("|")
    return parts[0], parts[1], parts[2]


def verb_list() -> None:
    ports = _scan_ports()
    if not ports:
        print("No boards connected.")
        return
    for port in ports:
        label, ver, ts = _read_board_info(port)
        label = label or "[unlabeled]"
        if not ver:
            status = "(no manifest)"
        else:
            status = f"{ver}  {ts}"
        print(f"  {label:<14}  {port:<32}  {status}")


def find_port(label: str) -> str | None:
    """Return the serial port whose /flash/device_label.txt matches label."""
    for port in _scan_ports():
        r = _run(["connect", port, "exec",
                  "try:\n print(open('/flash/device_label.txt').read().strip())\nexcept: print('')"])
        candidate = (r.stdout or "").strip().splitlines()
        got = candidate[-1].strip() if candidate else ""
        if got == label:
            return port
    return None


def _mpremote_exec(port: str, snippet: str, timeout: int = 10) -> str:
    r = _run(["connect", port, "exec", snippet], timeout=timeout)
    out = r.stdout or ""
    lines = out.splitlines()
    return lines[-1].strip() if lines else ""


def verb_state(port: str, label: str) -> None:
    snippet = r"""
import gc, json, os
def _rd(p):
    try:
        f=open(p,'rb'); d=f.read(); f.close(); return d.decode()
    except: return ''
def _ex(p):
    try: os.stat(p); return True
    except: return False

last_state = _rd('/flash/last_state.txt').strip()
history_raw = _rd('/flash/reboot_history.txt').strip()
ring_raw = _rd('/flash/breadcrumb_ring.txt').strip()
debug_mode = _ex('/flash/debug_mode.txt')
watchdog = _ex('/flash/watchdog.txt')
free_ram = gc.mem_free()
try:
    import machine; rc = machine.reset_cause()
except: rc = -1
try: os.listdir('/sd'); sd_own='MOUNTED'
except: sd_own='NO_CARD'
try: os.listdir('/sd2'); sd_guest='MOUNTED'
except: sd_guest='NO_CARD'
print(json.dumps({'ls':last_state,'h':history_raw,'r':ring_raw,'dm':debug_mode,'wd':watchdog,'ram':free_ram,'rc':rc,'so':sd_own,'sg':sd_guest}))
"""
    raw = _mpremote_exec(port, snippet, timeout=8)
    try:
        d = json.loads(raw)
    except Exception:
        print(f"== {label} ({port}) ==")
        print("Live state:       UNRESPONSIVE (REPL did not respond)")
        print("                  -> hold PWR for 5 s to force reboot, then re-run.")
        return

    # Parse reboot history
    history_lines = [l for l in d["h"].splitlines() if l.strip()]
    last_boot_line = history_lines[-1] if history_lines else None
    last_boot_ts = ""
    last_boot_cause = ""
    if last_boot_line:
        parts = last_boot_line.split()
        if len(parts) >= 3:
            ts = int(parts[0])
            last_boot_cause = parts[2]
            last_boot_ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Summarize boot history
    cause_counts: dict[str, int] = {}
    for l in history_lines:
        parts = l.split()
        if len(parts) >= 3:
            cause_counts[parts[2]] = cause_counts.get(parts[2], 0) + 1
    history_summary = ", ".join(
        f"{v}× {k}" for k, v in sorted(cause_counts.items(), key=lambda x: -x[1])
    ) + f" in last {len(history_lines)} boot(s)"

    # Parse last breadcrumb and compute staleness
    crumb = d["ls"]
    crumb_age = ""
    if crumb:
        parts = crumb.split(" ", 3)
        if len(parts) == 4:
            try:
                crumb_ts = int(parts[0])
                boot_ts_val = int(history_lines[-1].split()[0]) if last_boot_line else 0
                age = boot_ts_val - crumb_ts if boot_ts_val > crumb_ts else 0
                crumb_age = f"({age} s before last boot)" if age > 0 else "(after last boot)"
                crumb = parts[3]
            except Exception:
                pass

    rc_name = _RESET_CAUSE_NAMES.get(d["rc"], str(d["rc"]))
    free_kb = d["ram"] // 1024

    print(f"== {label} ({port}) ==")
    print(f"Last boot:        {last_boot_ts} (cause: {last_boot_cause or rc_name})")
    print(f"Boot history:     {history_summary}")
    print(f"Last breadcrumb:  {crumb}")
    if crumb_age:
        print(f"                  {crumb_age}")

    ring_lines = [l for l in d.get("r", "").splitlines() if l.strip()]
    ring_labels = []
    for l in ring_lines[-10:]:
        parts = l.split(" ", 3)
        ring_labels.append(parts[3] if len(parts) == 4 else l)
    if ring_labels:
        print(f"Recent trail:     (last {len(ring_labels)} from /flash/breadcrumb_ring.txt)")
        for label_ in ring_labels:
            print(f"                    {label_}")

    print(f"Live state:       responsive")
    print(f"SD own:           {d['so']}")
    print(f"SD guest:         {d['sg']}")
    print(f"Free RAM:         {free_kb} KB")
    print(f"Debug mode:       {'ON' if d['dm'] else 'OFF'}")
    print(f"Watchdog:         {'ENABLED (8 s)' if d['wd'] else 'DISABLED'}")


def verb_click(port: str, x: int, y: int) -> None:
    # Write/append to /flash/inject_queue.txt via exec. The firmware's
    # get_touch() drains this file when debug_mode is on.
    snippet = f"""
import os
try: os.stat('/flash/debug_mode.txt'); dm=True
except: dm=False
p='/flash/inject_queue.txt'
try:
    f=open(p,'rb'); e=f.read(); f.close()
except OSError: e=b''
f=open(p,'wb'); f.write(e+b'{x},{y}\\n'); f.close()
print('dm='+str(dm))
"""
    raw = _mpremote_exec(port, snippet)
    print(f"Queued touch: ({x}, {y})")
    if "dm=False" in raw:
        print("  WARNING: debug mode is OFF on device - click will be ignored by firmware.")
        print("  Run: inspect.sh <label> enable-debug")


def verb_type(port: str, text: str, kb: str = "qwerty") -> None:
    """Type text by injecting a sequence of clicks."""
    if kb == "digits":
        layout = _DIGIT_MAP
        for ch in text:
            xy = layout.get(ch)
            if xy:
                verb_click(port, xy[0], xy[1])
                time.sleep(0.1)
            else:
                print(f"  warning: no key for {ch!r} in digits layout", file=sys.stderr)
        return

    if kb == "hex":
        layout = _HEX_MAP
        for ch in text.upper():
            xy = layout.get(ch)
            if xy:
                verb_click(port, xy[0], xy[1])
                time.sleep(0.1)
            else:
                print(f"  warning: no key for {ch!r} in hex layout", file=sys.stderr)
        return

    # qwerty: handle uppercase, numbers, space
    on_numbers = False
    for ch in text:
        if ch == ' ':
            verb_click(port, _QWERTY_SPACE[0], _QWERTY_SPACE[1])
            time.sleep(0.1)
            continue
        if ch == '\n':
            verb_click(port, _QWERTY_DONE[0], _QWERTY_DONE[1])
            time.sleep(0.1)
            continue

        lower = ch.lower()
        in_num_map = ch in _NUM_MAP
        in_qwerty = lower in _QWERTY_MAP

        if in_num_map and not in_qwerty:
            if not on_numbers:
                verb_click(port, _QWERTY_NUM[0], _QWERTY_NUM[1])
                time.sleep(0.15)
                on_numbers = True
            xy = _NUM_MAP[ch]
            verb_click(port, xy[0], xy[1])
            time.sleep(0.1)
        else:
            if on_numbers:
                verb_click(port, _NUM_ABC[0], _NUM_ABC[1])
                time.sleep(0.15)
                on_numbers = False
            is_upper = ch.isupper()
            if is_upper:
                verb_click(port, _QWERTY_SHIFT[0], _QWERTY_SHIFT[1])
                time.sleep(0.1)
            xy = _QWERTY_MAP.get(lower)
            if xy:
                verb_click(port, xy[0], xy[1])
                time.sleep(0.1)
            else:
                print(f"  warning: no key for {ch!r} in qwerty layout", file=sys.stderr)
            # shift auto-releases after one char in the keyboard widget


def verb_macro(port: str, name: str) -> None:
    macro_path = os.path.join(SCRIPT_DIR, "macros", name + ".txt")
    if not os.path.exists(macro_path):
        sys.exit(f"Macro not found: {macro_path}")
    with open(macro_path) as f:
        lines = f.readlines()
    for line in lines:
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        if cmd == "click":
            x, y = args.split()
            verb_click(port, int(x), int(y))
        elif cmd == "type":
            kb = "qwerty"
            text = args.strip()
            if args.startswith("qwerty ") or args.startswith("digits ") or args.startswith("hex "):
                kb, text = args.split(None, 1)
            verb_type(port, text.strip('"'), kb=kb)
        elif cmd == "sleep":
            time.sleep(float(args))
        else:
            print(f"  unknown macro command: {cmd!r}", file=sys.stderr)


def _toggle_flag(port: str, path: str, on: bool) -> None:
    if on:
        snippet = f"open('{path}','w').close()"
    else:
        snippet = f"import os\ntry: os.remove('{path}')\nexcept: pass"
    _mpremote_exec(port, snippet)
    state = "created" if on else "deleted"
    print(f"{path} {state}")
    print("Rebooting...")
    _run(["connect", port, "reset"])


def main():
    if not os.path.exists(MPREMOTE):
        sys.exit(f"mpremote not found at {MPREMOTE}\nRun: cd scripts && python3 -m venv venv && venv/bin/pip install -r requirements.txt")

    # `list` has no label argument - handle it before argparse.
    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        verb_list()
        return

    p = argparse.ArgumentParser(description="OTP gadget debug inspector")
    p.add_argument("label", help="Board label (e.g. alice), or 'list' to list boards")
    p.add_argument("verb", choices=[
        "state", "click", "type", "macro",
        "enable-debug", "disable-debug",
        "watchdog-on", "watchdog-off",
        "reboot", "tail-prints",
    ])
    p.add_argument("args", nargs="*")
    p.add_argument("--kb", choices=["qwerty", "digits", "hex"], default="qwerty",
                   help="Keyboard layout for 'type' verb")
    opts = p.parse_args()

    port = find_port(opts.label)
    if not port:
        sys.exit(f"Board '{opts.label}' not found. Connect it and try again.")

    v = opts.verb
    a = opts.args

    if v == "state":
        verb_state(port, opts.label)
    elif v == "click":
        if len(a) != 2:
            sys.exit("Usage: inspect.sh <label> click X Y")
        verb_click(port, int(a[0]), int(a[1]))
    elif v == "type":
        if not a:
            sys.exit("Usage: inspect.sh <label> type \"text\"")
        verb_type(port, " ".join(a), kb=opts.kb)
    elif v == "macro":
        if not a:
            sys.exit("Usage: inspect.sh <label> macro <name>")
        verb_macro(port, a[0])
    elif v == "enable-debug":
        _toggle_flag(port, "/flash/debug_mode.txt", on=True)
    elif v == "disable-debug":
        _toggle_flag(port, "/flash/debug_mode.txt", on=False)
    elif v == "watchdog-on":
        _toggle_flag(port, "/flash/watchdog.txt", on=True)
    elif v == "watchdog-off":
        _toggle_flag(port, "/flash/watchdog.txt", on=False)
    elif v == "reboot":
        _run(["connect", port, "reset"])
        print(f"{opts.label} rebooted.")
    elif v == "tail-prints":
        print(f"Streaming stdout from {opts.label} ({port})... Ctrl-C to stop.")
        subprocess.run([MPREMOTE, "connect", port, "repl"])


if __name__ == "__main__":
    main()
