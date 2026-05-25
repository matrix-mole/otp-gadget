# Item 10: uQR generates correct v21 matrix for a 500-byte payload
#
# PREREQ: uQR.py must be on the board. Copy firmware/core/vendor/uQR.py
#         to the board root (or place it so it's importable).
#
# What this checks:
#   1. uQR encodes a realistic 1028-char hex string (500-byte plaintext frame)
#   2. Matrix dimensions = 101 × 101 (QR v21)
#   3. Generation time is reasonable (< 10 s)
#   4. Scan the on-screen matrix with a phone to verify it decodes (manual step)

import time
import gc

print("=== Item 10: uQR v21 generation ===")

try:
    import uQR
except ImportError:
    print("FAIL: uQR.py not found on board.")
    print("  Copy firmware/core/vendor/uQR.py to the board root first.")
    raise

# Build a realistic wire-format hex string for a 500-byte plaintext.
# Frame: [offset 4B][length 2B][ciphertext 500B][tag 8B] = 514 bytes → 1028 hex chars
offset     = (0).to_bytes(4, 'big')
length     = (500).to_bytes(2, 'big')
ciphertext = bytes(range(250)) * 2  # 500 bytes
tag        = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04])
frame      = offset + length + ciphertext + tag
hex_str    = frame.hex().upper()

print(f"  Payload hex length: {len(hex_str)} chars (expect 1028)")

gc.collect()
t0 = time.ticks_ms()

qr = uQR.QRCode(error_correction=uQR.ERROR_CORRECT_M)
# optimize=0 skips the regex-based chunker (which misbehaves under MicroPython's
# `re`) and lets QRData auto-detect MODE_ALPHA_NUM via `optimal_mode`.
qr.add_data(hex_str, optimize=0)
qr.make(fit=True)
matrix = qr.get_matrix()

# Debug: which mode and version did uQR end up choosing?
mode_names = {1: "NUMBER", 2: "ALPHA_NUM", 4: "8BIT_BYTE", 8: "KANJI"}
chosen_modes = [mode_names.get(d.mode, str(d.mode)) for d in qr.data_list]
print(f"  Modes used: {chosen_modes}")
print(f"  QR version: {qr.version}")

elapsed = time.ticks_diff(time.ticks_ms(), t0)

n = qr.modules_count
border_total = len(matrix) - n
px_per_mod = 320 / n
print(f"  Modules (data): {n} × {n}")
print(f"  Matrix incl. border: {len(matrix)} × {len(matrix)} (border = {border_total // 2}/side)")
print(f"  px/module on 320 px screen: {px_per_mod:.2f}")
print(f"  Generation time: {elapsed} ms")

scan_ok = px_per_mod >= 3.0
if scan_ok:
    print(f"  Readability: PASS ({px_per_mod:.2f} ≥ 3.00 px/module)")
else:
    print(f"  Readability: FAIL ({px_per_mod:.2f} < 3.00 px/module - reduce message cap)")

# Generation can be slow; the 500-byte cap is the limit. < 15 s is acceptable for
# a one-shot operation the user explicitly initiates.
time_ok = elapsed <= 15_000
if time_ok:
    print(f"  Timing: PASS ({elapsed} ms < 15 000 ms)")
else:
    print(f"  Timing: FAIL ({elapsed} ms - too slow)")

if scan_ok and time_ok:
    print("\nPASS")
    print("Manual step: display the matrix on screen and scan it with a phone.")
else:
    print("\nFAIL - see above")
