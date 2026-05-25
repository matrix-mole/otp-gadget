# QR Sizing Spike Results

Run on 2026-04-22, macOS, Python 3.13.

## Findings

| Plaintext (bytes) | Hex chars | QR modules | px/module (320 px screen) | ≥3 px? |
|---|---|---|---|---|
| 200 | 428 | 69 | 4.6 | YES |
| 300 | 628 | 81 | 4.0 | YES |
| 400 | 828 | 93 | 3.4 | YES |
| **500** | **1028** | **101** | **3.2** | **YES** |
| 600 | 1228 | 109 | 2.9 | NO |
| 750 | 1528 | 121 | 2.6 | NO |

Hex frame = `[offset 4B][length 2B][ciphertext NB][tag 8B]` hex-encoded uppercase, ECC level M.

## Decision

**Plaintext cap: 500 bytes.** Gives QR v21 (101 modules, ~3.2 px/module on 320 px screen). 600 bytes drops below the 3.0 px/module threshold.

## Library decision

`adafruit_miniqr` only supports QR versions 1–9 and cannot encode any of the above payloads.
**Use `uQR` on the board** (MicroPython-compatible, correct version range).
The `qrcode` PyPI package was used for this desktop measurement.

## On-board verification (uQR)

Re-checked on the RP2350 with uQR - see `firmware/setup/rp2350-bringup-log.md`
item 10. uQR picks **v22 (105 modules)** for the 500-byte payload, one version
larger than desktop `qrcode` (v21, 101 modules). At 320 px that is 3.05
px/module - still above the 3.0 readability threshold, so the 500-byte cap
holds. The two libraries differ slightly in how they pack length-field bits,
not in correctness.
