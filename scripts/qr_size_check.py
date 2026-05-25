"""
QR sizing spike - see firmware/README.md § "QR Sizing Spike" for full rationale.

For each candidate plaintext size, builds a realistic wire frame
(offset + length + ciphertext + tag, hex-encoded uppercase) and feeds it to
adafruit_miniqr and uQR. Prints a table so we can pick the largest plaintext
size that still gives ≥3 px/module on the 320 px screen.

Run via:
    cd scripts && ./run.sh
"""

import os
import struct

# ── adafruit_miniqr ──────────────────────────────────────────────────────────
try:
    import adafruit_miniqr

    def qr_adafruit(data: str):
        qr = adafruit_miniqr.QRCode()
        qr.add_data(data)
        if qr.type is None:
            return None  # data too large for adafruit_miniqr (supports v1–9 only)
        qr.make()
        n = len(qr.matrix)
        return n

except ImportError:
    qr_adafruit = None

# ── qrcode (desktop reference, explicit alphanumeric mode) ───────────────────
try:
    import qrcode
    import qrcode.constants

    def qr_uqr(data: str):
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(data, optimize=0)  # optimize=0 forces the added mode; let it auto-pick best
        qr.make(fit=True)
        n = qr.modules_count
        return n

except ImportError:
    qr_uqr = None

# ── helpers ──────────────────────────────────────────────────────────────────

SCREEN_PX = 320
HEADER_BYTES = 4 + 2  # offset (4) + length (2)
TAG_BYTES = 8          # HMAC-SHA256 truncated to 8 bytes

def build_hex_frame(plaintext_len: int) -> str:
    offset = 0
    length = plaintext_len
    ciphertext = os.urandom(plaintext_len)
    tag = os.urandom(TAG_BYTES)
    frame = struct.pack(">IH", offset, length) + ciphertext + tag
    return frame.hex().upper()

def px_per_module(n_modules: int) -> float:
    return SCREEN_PX / n_modules

def version_from_modules(n: int) -> int:
    return (n - 17) // 4

# ── main ─────────────────────────────────────────────────────────────────────

CANDIDATES = [200, 300, 400, 500, 600, 750]

header = f"{'Plaintext':>12}  {'Hex chars':>10}  {'adafruit mod':>13}  {'adafruit px/mod':>16}  {'qrcode mod':>10}  {'qrcode px/mod':>13}  {'≥3px (adafruit)':>15}  {'≥3px (qrcode)':>13}"
print(header)
print("-" * len(header))

for pt_len in CANDIDATES:
    hex_frame = build_hex_frame(pt_len)
    hex_len = len(hex_frame)

    af_n = qr_adafruit(hex_frame) if qr_adafruit else None
    uq_n = qr_uqr(hex_frame) if qr_uqr else None

    af_px = f"{px_per_module(af_n):.1f}" if af_n else "N/A"
    uq_px = f"{px_per_module(uq_n):.1f}" if uq_n else "N/A"
    af_ok = ("YES" if af_n and px_per_module(af_n) >= 3.0 else "NO ") if af_n else "N/A"
    uq_ok = ("YES" if uq_n and px_per_module(uq_n) >= 3.0 else "NO ") if uq_n else "N/A"
    af_mod = str(af_n) if af_n else "N/A"
    uq_mod = str(uq_n) if uq_n else "N/A"

    print(
        f"{pt_len:>12}  {hex_len:>10}  {af_mod:>13}  {af_px:>16}  {uq_mod:>10}  {uq_px:>13}  {af_ok:>15}  {uq_ok:>13}"
    )

print()
print("Target: ≥3.0 px/module on a 320 px screen.")
print("Pick the largest plaintext size where both libraries show YES (or the better one if they differ).")
print("Write results to scripts/qr_size_check_results.md and update the cap in firmware/README.md and README.md.")
