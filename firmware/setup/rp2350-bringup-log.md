# RP2350 Hardware Bring-up Log

Board: Waveshare RP2350-Touch-LCD-3.5
MicroPython: v1.28.0 (2026-04-06) - `firmware/setup/RPI_PICO2-20260406-v1.28.0.uf2`
Date: 2026-04-25

## Checklist results

### 1 - `import cryptolib` - PASS

### 2 - CTR mode (mode 6) - FAIL

```
>>> cryptolib.aes(b'\x00'*32, 6, b'\x00'*16)
ValueError: mode
```

ECB mode (mode 1) is available:

```
>>> cryptolib.aes(b'\x00'*32, 1)
<aes>
```

**Decision:** implement AES-CTR manually using AES-ECB. For each 16-byte block N, keystream is `AES-ECB(DEK, IV + N)`, XORed with the ciphertext. This covers both sequential encryption and random-access pad slicing.

### 12 - `import hmac` - FAIL

```
>>> import hmac
ImportError: no module named 'hmac'
```

**Decision:** write a small wrapper around `hashlib.sha256` using the standard HMAC construction.

### 3 - ECB-based CTR seek - PASS

Verified that the keystream-via-ECB approach roundtrips correctly and that decrypting a mid-buffer slice with the right starting block index matches the plaintext slice. Confirms `read_secret_slice` can be implemented with manual ECB.

```
Full roundtrip: True
Slice match: True
```

### 4 - PBKDF2 throughput - done

Pure-Python PBKDF2-HMAC-SHA256 (using the manual HMAC wrapper) measured at:

```
1000 iters: 1658 ms
iters/sec: 603
```

Slower than the README's pre-measurement guess of 5,000–10,000 iters for 1–2 s. The HMAC wrapper does 2 SHA-256 calls per iteration, and SHA-256 in pure-Python on RP2350 is the bottleneck.

**Decision:** default iteration count = **1000** (≈1.66 s on this board). Stored per card in `/device/kdf_params.json` so it can be raised later without breaking existing cards.

### MicroPython firmware switch (mid-bring-up)

The initial flash used `RPI_PICO2-20260406-v1.28.0.uf2`, which is the build for
the standard Pi Pico 2 (RP2350**A**, only 30 GPIOs). On that build,
`Pin(31, Pin.OUT)` raises `ValueError: invalid pin` - and the Waveshare board
needs GPIO31 (onboard SD CS), GPIO32–33 (header SPI/UART), GPIO34/35
(touch I2C), etc.

Reflashed with `docs/waveshare-examples-repo/firmware/MicroPython/WAVESHARE_RP2350_Touch_LCD_3.5.uf2`,
the Waveshare-specific build for the RP2350**B** (full 48 GPIOs). All later
items are run on that build. The `RPI_PICO2` UF2 in `firmware/setup/` should
not be used for this board.

### 8 - Display (ST7789) + touch (FT6336U) - PASS

Test script: `firmware/setup/check_08_display_touch.py`. Requires `ST7789.py`
and `FT6336U.py` from the Waveshare examples on the board.

- Display flashes blue → red → black correctly (drivers/SPI on GPIO18-23 OK)
- Touch chip ID = `0x64` (correct, FT6336U on I2C1 GPIO34/35)
- 3 taps registered with sensible coordinates
- No I2C contention between touch and any other onboard peripheral

### 8b - Display orientation (MADCTL fix)

After running the full UI on the board, the display was horizontally mirrored
relative to the simulator (text reads right-to-left). The Waveshare example
ships MADCTL (`0x36`) = `0x08` (BGR, no axis flip), which on this panel
produces a mirrored portrait image.

**Fix:** MADCTL = `0x48` in `firmware/hal/drivers/st7789.py` - sets the MX bit
(column address direction reversed) on top of the BGR bit. Do not revert this
to match the upstream Waveshare example.

**Touch alignment check:** ran `firmware/setup/check_13_touch_alignment.py`
after the MADCTL fix. The original assumption in `real.py.get_touch` - that
the FT6336U reports landscape coordinates (`x` in 0..479, `y` in 0..319) and
must be rotated 90° - was wrong. The chip on this board reports **portrait
coordinates directly** (`x` in 0..319, `y` in 0..479). Five-target sweep
showed `raw_x ≈ portrait_x`, `raw_y ≈ portrait_y` to within finger-precision:

```
Target (30, 30)    raw (34, 45)
Target (290, 30)   raw (301, 38)
Target (160, 240)  raw (162, 246)
Target (30, 450)   raw (28, 441)
Target (290, 450)  raw (282, 450)
```

**Fix:** `get_touch` now returns `(raw_x, raw_y)` - no rotation. The
Waveshare example does `x = LCD_WIDTH - p['x']` because of how they orient
their demo, not because the chip needs rotation.

### 9 - Free SRAM after representative allocations - PASS

Test script: `firmware/setup/check_09_sram.py`. Allocates worst-case in-flight
buffers (101×101 QR matrix, keyboard state, 4 KB streaming chunk, 500-byte
plaintext) and reports `gc.mem_free()` at each step.

```
Free at start:         488,608 bytes  (477 KB)
After QR matrix alloc: 444,432 bytes  (used ~43 KB)
After keyboard alloc:  444,176 bytes  (used ~0 KB)
After 4 KB chunk buf:  440,048 bytes  (used ~4 KB)
After 500-byte msg:    439,472 bytes  (used ~0 KB)
Minimum free observed: 429 KB
```

Plenty of headroom (≥100 KB threshold; 429 KB observed).

### 10 - uQR generation for 500-byte payload - PASS

Test script: `firmware/setup/check_10_uqr.py`. Encodes a realistic 1028-char
hex frame (`offset 4B + length 2B + ciphertext 500B + tag 8B`).

```
Modes used: ['ALPHA_NUM']
QR version: 22
Modules (data): 105 × 105
px/module on 320 px screen: 3.05
Generation time: 10267 ms
```

**Two MicroPython compatibility issues found and patched in `uQR.py`:**

1. `QRData.write()` for `MODE_ALPHA_NUM` did `ALPHA_NUM.find(chars[0])`. In
   MicroPython, indexing into `bytes` yields `int`, and `bytes.find(int)`
   raises `TypeError`. Patched to use 1-byte slices: `chars[0:1]`, `chars[1:2]`.
2. `optimal_data_chunks` uses regex patterns that don't behave the same under
   MicroPython's `re` module - it falls through to `MODE_8BIT_BYTE` instead of
   `MODE_ALPHA_NUM` for hex strings. Workaround: callers must pass
   `optimize=0` to `add_data()`, which routes through `optimal_mode()` (a
   simple membership check) and correctly picks `MODE_ALPHA_NUM`.

**Discrepancy with desktop spike:** the desktop `qrcode` library picked v21
(101 modules) for the same payload; uQR picks v22 (105 modules). Both are
above the 3.0 px/module readability threshold on a 320 px screen. The 500-byte
plaintext cap holds. Generation time is ~10 s - acceptable for a one-shot
operation the user explicitly initiates.

### 11 - 5 MB pad write benchmark - PASS (after sdcard.py CRC fix)

Test script: `firmware/setup/check_11_pad_write_bench.py`. With the
Intenso 32 GB SDHC in the onboard slot:

```
Write done: 15288 ms  →  334 KB/s   (5 MB written in 4 KB chunks)
Read done:  70893 ms  →   72 KB/s   (5 MB read back, verified)
```

Write speed (334 KB/s) is plenty: 5 MB pad init takes ~15 s on first
exchange, and the per-message write footprint is tiny. Read speed (72 KB/s)
is slower but the firmware never does full-pad reads - per-message reads are
<1 KB via `read_secret_slice` with seek-to-block.

**Root-cause investigation - non-trivial.** The shipped `sdcard.py` driver
timed out at `init_card_v2` (`OSError: timeout waiting for v2 card`). All of
`CMD58`, `CMD55`, and `ACMD41` returned `-1` (no R1 byte at all). CMD0/CMD8
both worked. The card mounts cleanly on macOS.

**The bug:** every SD command frame's last byte ends with a stop bit (LSB =
1). `CMD0` and `CMD8` use `crc=0x95` and `crc=0x87` (LSB = 1, valid). All
other commands in the legacy driver pass `crc=0` (LSB = 0, **invalid stop
bit**). Most SD cards tolerate this; some - including this Intenso 32 GB -
silently refuse to respond.

**Fix:** patched `docs/waveshare-examples-repo/examples/MicroPython/02_SD/sdcard.py`
to pass `crc=0x01` (CRC7 = 0, stop bit set) instead of `crc=0` for every
command where CRC checking is don't-care (which is all of them in SPI mode
once CMD0 has been seen). Also bumped `_CMD_TIMEOUT` from 100 → 1000 as
belt-and-suspenders for slow cards (MicroPython issue #7129).

`real.py` will need to ship the patched `sdcard.py` - the unpatched Waveshare
copy will not work with the Intenso card.

## CTR mode fallout - master_key.py fix

`firmware/core/crypto/master_key.py` originally called `cryptolib.aes(key, 6, iv)`
(CTR mode) which is not available (see item 2 above). This would cause PIN unlock to
hard-fail the first time it ran on the board.

Fix (implemented in task 7.3a): the manual ECB-CTR construction is extracted into
`firmware/core/crypto/ctr.py` and `master_key.py` is updated to call `aes_ctr_xor`
from there. The sim continues to use pycryptodome's native CTR (separate code path,
same on-disk bytes). `real.py`'s `read_secret*` / `write_secret*` methods use the
same `ctr.py` helpers.

## Guest SD module compatibility (item 6 follow-up)

During end-to-end testing of two physical prototypes, both guest SD slots failed
to initialize with `OSError: timeout waiting for v2 card` after a ~50 s retry
loop. Symptoms:

- CMD0 succeeds (card responds with idle state) → SPI wiring + clocking are fine
- CMD8 succeeds (card identifies as v2) → bus is bidirectional at 100 kHz init
- ACMD41 loop never reaches "ready" → card never finishes internal init

Root cause was the **SD breakout module**, not the firmware, not the wiring, not
the cards. The two AliExpress modules ordered (items 1005001309671718 and
32346771288) both advertise "5V/3.3V" but in fact require **4.5–5.5 V on VCC**:
they have an onboard AMS1117-3.3 regulator with a ~1.0–1.3 V dropout. When VCC
is wired to the Waveshare header's 3V3 pin (the only power available on
battery), the regulator's output droops to ~2.0–2.3 V - enough for CMD0/CMD8's
tiny current draw, but **below the SD spec minimum** during ACMD41 init when
the card briefly draws ~50–100 mA.

The compatible module is **AliExpress item 1005010794549615** ("Generic MicroSD
SPI module 3.3V", 18.5×17.5 mm, no chips on the board - just the SD slot wired
straight to the 6-pin header). VCC goes to header pin 31 or 32 (3V3); no
regulator, no level shifter, works on USB and battery alike.

Documented in `docs/parts-and-products/index.md` ("SD Card Modules"), the root
`README.md` parts list, and `docs/datasheets/3.5inch-touchscreen/pin-diagram.md`
so the next person ordering parts doesn't re-discover this.

## Item 7 follow-up - GM861XS QR scanner wiring + bring-up (2026-05-13)

First physical bring-up of the GM861XS over UART1 (GPIO4 TX, GPIO5 RX). Three
findings worth recording so the next person doesn't re-debug them:

### a) Pin-diagram TX/RX labels were ambiguous

`docs/datasheets/3.5inch-touchscreen/pin-diagram.md` originally labelled pin
14/16 as "QR TX (UART1)" / "QR RX (UART1)" - readable two ways. The natural
reading "this is where the scanner's TX wire plugs in" is wrong; the correct
reading is "this is the board's TX line, which talks to the scanner's RX".
Fixed by changing the labels to `UART1 TX → QR RX` / `UART1 RX ← QR TX` with
matching arrows in the summary table and root `README.md` GPIO table. Test
script `firmware/setup/check_07_qr_scanner.py` (heartbeat) confirms wiring.

### b) Response-CRC offset bug in `hal/real.py::_qr_read_zone`

The scanner CRCs **request** frames over the full body (`Type+Len+Address+Data`),
but **response** frames are CRC'd over `Len+Address+Data` only, *excluding* the
leading Type byte at index 0. Verified empirically: response
`02 00 00 01 D6 98 8A` matches CRC over bytes 1..5, not 0..5.

`real.py` originally computed CRC over `rsp[:5]`, which would have thrown
`bad CRC` the first time `_qr_ensure_config` ran on real hardware. Fixed to
`rsp[1:5]`.

### c) Scanner ships with output routed to USB HID-KBW (zone 0x000D bit 0)

This was the headline gotcha. Symptom: heartbeat works, register reads/writes
work, trigger command is ACKed, the indicator LED flashes green on a valid QR
(decode succeeds internally) - but **no decoded data ever reaches the UART**.

Zone `0x000D` bits[1:0] control output routing:

| Value | Routing                  |
|-------|--------------------------|
| `00`  | Serial port output       |
| `01`  | USB PC Keyboard (HID-KBW)|
| `10`  | Keep / reserved          |
| `11`  | USB virtual serial port  |

The factory default on the GM861XS unit ordered for this project was `01`
(HID-KBW) - i.e. decoded data is emitted as USB HID keystrokes, which is useless
on a UART-only wiring. Even though the GM861XS module has no USB pins broken
out, the firmware default still routes there and the UART data path stays
silent.

Fix: `_qr_ensure_config` now also clears bits[1:0] of zone `0x000D`, forcing
serial-port output. Saved to flash on first boot. Validated end-to-end with
`firmware/setup/check_12_qr_scan_loop.py` - a "hello world" QR on a phone
screen decodes as expected:

```
+856ms recv 20B: 0200000100333168656c6c6f20776f726c640d0a
>>> decoded: 'hello world'
```

(The leading `02000001003331` is the trigger-ACK; the trailing `0d0a` is the
CR+LF tail configured via zone `0x0060`.)

## Remaining items

- Items 5–6: pending end-to-end exercise of the guest SD slot in the live
  exchange flow (wiring + driver already proven; see "Guest SD module
  compatibility" above).
