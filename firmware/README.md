# Firmware

Written in **MicroPython**. Deployed to hardware via `scripts/flash.sh` (uses `mpremote`; see [`scripts/README.md`](../scripts/README.md) for usage). Thonny IDE is used only for interactive debugging/REPL.

## Index

- `core/` - pure Python business logic (OTP, SD data structure, message flows). Runs anywhere.
- `hal/` - hardware abstraction layer (see below)
  - `real.py` - real hardware implementation (MicroPython, runs on board)
  - `sim.py` - simulated implementation (runs on laptop)
  - `drivers/` - vendored MicroPython drivers (ST7789, FT6336U, sdcard, AXP2101, PCF85063) bundled with the firmware
- `sim/` - localhost web app simulating the touchscreen UI (Flask)
- `tests/` - unit tests for core logic
- `setup/` - board setup scripts and one-time config
- `main.py` - board entry point: constructs `RealHAL` and calls `main_loop`

## Architecture

All core logic lives in `core/` as plain Python - no hardware calls, no MicroPython-specific code. Hardware is accessed exclusively through the **hardware abstraction layer (HAL)**: a small interface with two implementations, `real.py` and `sim.py`. The core never knows which one it's talking to.

**HAL responsibilities:**
- `get_random_bytes(n)` - TRNG on board, `os.urandom()` in sim
- SD card read/write - real SPI/onboard TF on board, local filesystem in sim
- Display output - real screen on board, served to browser in sim
- Touch input - capacitive touch on board, mouse clicks in browser in sim
- QR scanner input - UART on board, text input or file upload in sim

## HAL Interface

All method signatures that `core/` may call. Both `real.py` and `sim.py` must implement every method exactly.

```python
# Entropy
hal.get_random_bytes(n: int) -> bytes

# Display - portrait 320×480; all rotation handled inside the driver
hal.fill_rect(x: int, y: int, w: int, h: int, color: int) -> None
hal.blit_rect(x: int, y: int, w: int, h: int, buf: bytes) -> None  # buf = RGB565 bytes

# Touch - portrait coords (origin top-left), or None if no touch
hal.get_touch() -> tuple[int, int] | None

# SD cards
hal.mount_card(slot: str) -> str          # slot='own'|'guest'; returns 'MOUNTED'|'NO_CARD'|'WRONG_FS'
hal.unmount_card(slot: str) -> None
hal.read_file(slot: str, path: str) -> bytes          # raises OSError if file missing; whole-file - small files only
hal.write_file(slot: str, path: str, data: bytes) -> None  # atomic: writes .tmp then renames; whole-file - small files only; auto-creates parent dirs
hal.delete_file(slot: str, path: str) -> None
hal.delete_tree(slot: str, path: str) -> None   # recursively delete a directory and all contents
hal.file_exists(slot: str, path: str) -> bool
hal.free_space(slot: str) -> int          # available bytes

# Plaintext streaming (guest-card OTP.bin, X_own.bin - multi-MB, do not fit in RAM)
hal.read_file_stream(slot: str, path: str, offset: int, length: int) -> Iterator[bytes]
hal.write_file_stream(slot: str, path: str) -> WriteHandle  # .write(data), .close(); atomic tmp+rename on close; auto-creates parent dirs

# Secrets key management - call unlock_secrets once after PIN derivation
hal.unlock_secrets(key: bytes) -> None   # stores derived key in RAM
hal.lock_secrets() -> None               # zeroes key from RAM

# Encrypted own-card secrets (AES-256-CTR) - requires unlock_secrets() first
hal.read_secret(path: str) -> bytes                                   # whole-file - small files only
hal.read_secret_slice(path: str, offset: int, length: int) -> bytes  # seeks to offset, decrypts only length bytes
hal.overwrite_secret_slice(path: str, offset: int, data: bytes) -> None  # in-place logical overwrite of decrypted bytes
hal.write_secret(path: str, data: bytes) -> None                      # whole-file; generates fresh IV on every write

# Encrypted streaming for pad files (pad_send.bin / pad_receive.bin - 5 MB each)
hal.read_secret_stream(path: str, offset: int, length: int) -> Iterator[bytes]  # CTR seek + chunked decrypt
hal.write_secret_stream(path: str) -> WriteHandle  # fresh IV at offset 0; .write(data), .close(); atomic tmp+rename

# MCU flash (device_secret, PIN attempt state)
hal.flash_read(path: str) -> bytes        # raises OSError if missing
hal.flash_write(path: str, data: bytes) -> None
hal.flash_exists(path: str) -> bool
hal.flash_delete(path: str) -> None       # no-op if missing; used by full factory reset

# QR scanner
hal.qr_ping() -> bool                    # True if scanner responds to heartbeat
hal.qr_scan() -> str | None              # triggers scan; returns hex string or None on timeout
hal.qr_poll() -> str | None              # non-blocking single check; first call triggers, subsequent calls return result when ready (resets after a value or after >1 s gap to handle re-entry)

# Battery
hal.battery_status() -> tuple[int, bool] # (percent 0–100, is_charging)

# Power management
hal.power_button_pressed() -> bool  # True once if a short press occurred since last call; clears flag on read
hal.power_off() -> None             # zeros DEK from RAM then triggers AXP2101 hardware shutdown; never returns
# Note: inserting a USB-C cable does NOT boot the device. RealHAL.__init__ reads
# PWRON_STATUS (reg 0x20) immediately after AXP init and calls axp.power_off() if
# the VBUS-only flag is set (bit 2) and the button flag is clear (bit 0). The AXP2101
# continues charging the battery; only the MCU powers down. See bring-up checklist #13.
hal.feed_watchdog() -> None         # pets the hardware watchdog; every UI loop must call this once per iteration. No-op in sim.

# Time - monotonic millisecond counter, wraps per MicroPython semantics
hal.ticks_ms() -> int                    # used for auto-lock idle timer and PIN-screen auto-power-off timer

# Wall-clock time from onboard RTC (PCF85063A) - survives power cycles
hal.rtc_now() -> int                     # unix seconds; used for PIN cooldown across reboots
```

**Filesystem note:** `write_file` and `write_file_stream` auto-create any missing parent directories (`/device/`, `/exchange/`, `/secret/`) so card init doesn't need a separate `mkdir` step. Same behavior in sim and on board.

**Time note:** `hal.ticks_ms()` is a monotonic millisecond counter used by core for the auto-lock idle timer and the PIN-screen auto-power-off timer. On board it wraps `time.ticks_ms()`; in sim it wraps `time.monotonic_ns() // 1_000_000`. Core must use `ticks_diff`-style subtraction (mod 2³⁰) and never compare raw values.

**RTC note:** `hal.rtc_now()` returns unix seconds from the onboard PCF85063A RTC, which runs off the LiPo and survives power cycles. Used only for the PIN cooldown, which must persist across reboots (`ticks_ms` resets on boot and cannot tell 10 s from 10 h). In sim it wraps `time.time()`. The RTC is set to a reasonable epoch at first boot; the absolute value doesn't need to be accurate, only monotonic across reboots.

## Screen Flow

The full set of screens and transitions. Source of truth - new screens get added here first.

```
Splash (gray screen + big gray "OTP", ~1.2 s) → Boot

Boot
 ├─ no /flash/device_secret.bin → DeviceSetup → (back to Boot)
 ├─ own card has no /secret/verify.bin → CardInit → (back to Boot)
 ├─ /exchange/ has staging on own card → IncompleteExchange (named after in_flight) → Home
 └─ otherwise → PINEntry

PINEntry → (reconcile_in_flight: silently clear stale markers) → Home
 └─ ? button (upper-right, ~36×36 px) → PINHelp modal  ← also shown on the cooldown screen
       ├─ "Restore using device secret backup" → PickRestoreMethod
       │     ├─ QR scan → verify entered value == /flash/device_secret.bin
       │     │     ├─ match    → SetNewPIN → re-derive KEK → re-wrap DEK → reset attempt counter → Home
       │     │     └─ no match → error: "Device secret does not match" → back to PickRestoreMethod
       │     └─ Manual hex entry (hex keypad) → same verify/branch as QR scan
       └─ "Wipe card & start fresh" → WipeConfirm (type RESET on QWERTY keyboard)
             ├─ cancelled → back to PINEntry
             └─ confirmed → two options:
                   ├─ "Wipe card only"        → delete /secret/ + /exchange/ → CardInit
                   └─ "Full factory reset"    → FactoryResetConfirm (second warning screen)
                         ├─ cancelled → back to WipeConfirm
                         └─ confirmed → delete /secret/ + /exchange/ + MCU flash (device_secret + PIN state) → re-enter main_loop → DeviceSetup

Home (3 buttons + gear icon top-right + lock icon top-left)
 ├─ Lock icon → immediate lock (DEK zeroed, history cleared) → PINEntry
 ├─ Send → ContactPicker → Keyboard → ShowCiphertext (QR + hex) → Home
 ├─ Receive → PickInputMethod
 │             ├─ QRScan → ShowPlaintext (auto-routed via HMAC trial; "From: <name>") → Home
 │             └─ Manual → Keyboard (hex) → ShowPlaintext → Home
 ├─ Contacts (list, created_at order; synthetic pending-add row prepended when in_flight.kind == "add")
 │    ├─ + Add contact → Keyboard (name; uniqueness checked) → set in_flight → exchange route
 │    │     ├─ guest has /exchange/X_own.bin → FinalizeExchange (B-side) → on success: commit + clear in_flight → Contacts
 │    │     └─ else                          → PrepareExchange (A-side; Cancel button aborts: deletes X_own.bin, clears in_flight) → Contacts
 │    │   (+ Add contact disabled while in_flight != null)
 │    ├─ tap Ready row → ContactThread (in-RAM messages with this contact, shown in full and line-wrapped, line-paginated newest-first; cleared on auto-lock)
 │    │     ├─ New message → Keyboard (with this contact) → ShowCiphertext → ContactThread
 │    │     └─ … menu → ContactDetail
 │    ├─ tap non-Ready row (Waiting / Ready to finalize / Re-exchange interrupted / pending add) → ContactDetail
 │    │     (action depends on runtime state - see ContactDetail)
 │    └─ Back → Home
 ├─ ContactDetail (status-driven)
 │    ├─ Ready                 → New message · Re-exchange · Delete contact
 │    ├─ Ready to finalize     → Finalize exchange (commits contact for kind="add"; clears in_flight)
 │    ├─ Waiting / Pending     → (no action; informational)
 │    ├─ Re-exchange interrupted → Restart re-exchange · Delete contact
 │    └─ Pending add (synthetic row) → Finalize exchange (when OTP.bin present) · Discard exchange
 └─ Settings (gear icon, top-right of Home)
      ├─ ChangePIN → Home
      ├─ ViewDeviceSecret → Home
      └─ BurnAfterReading row → ? help modal or toggle → Settings

AutoLock (5 min idle, any screen except DeviceSetup/CardInit) → PINEntry
AutoPowerOff (10 min idle at PINEntry) → device shuts down (AXP2101 power_off)
```

`IncompleteExchangeScreen` (boot-only modal) handles the "user power-cycled mid-exchange" case. Mid-session, the equivalent path is Contacts → tap row → ContactDetail → Finalize/Discard. There is no Home-level modal interrupt.

Battery icon and any active warnings (card status, scanner not detected) overlay on every screen except DeviceSetup, CardInit, and PINEntry.

The gear icon is a small touch target in the top-right of Home (roughly 36×36 px); the lock icon is a matching target in the top-left. Neither is repeated on subscreens - Settings is only reachable from Home; manual lock is only reachable from Home.

`IncompleteExchangeScreen` (the boot-time "Incomplete exchange with \<name>. Finalize or discard?" prompt, triggered when `/exchange/` has leftover staging on own card) reads `in_flight.name` from the manifest. Returns to Home after finalize (which commits the contact for `kind == "add"`) / discard (which clears `in_flight` and wipes staging). From there the user can navigate to Contacts to see the new contact or, if discarded, confirm it didn't land in the list.



## Touch

**Chip:** FT6336U on I2C1 (GPIO34/35 - SDA/SCL), with reset on GPIO24 and the chip's interrupt line on GPIO25 (left unused - see "Polling, not IRQ" below). Vendored driver at `firmware/hal/drivers/ft6336u.py`. The HAL wraps it as `get_touch() -> tuple[int, int] | None`.

**Coordinate mapping:** The FT6336U on this board reports portrait coordinates directly (x in 0..319, y in 0..479) - no rotation is applied in either the driver or the HAL. Verified by the five-target sweep in `check_13_touch_alignment.py`; see bring-up log item 8b.

**Polling, not IRQ:** Each call to `hal.get_touch()` polls the FT6336U over I2C from the main thread (TD_STATUS + the four touch-coordinate bytes). The chip's INT line on GPIO25 is wired but no interrupt handler is attached.

The shared I2C1 bus also carries traffic for the AXP2101 (battery + power-button bit) and the PCF85063 RTC. An older revision attached a hard IRQ to GPIO25 that performed I2C reads from inside the handler; on this bus that race-collided with main-thread I2C transactions to the AXP2101 / RTC and could leave the FT6336U (or the bus itself) wedged. Observed symptoms: touch stops registering, the short-press power-off bit (also polled over I2C in the main loop) is never seen, and the device appears frozen until the user holds PWR for ~5 s to trigger the AXP2101 hardware shutdown. Keeping all I2C access on one thread removes the race entirely. The watchdog (see below) is the second-line defense if anything else hangs.

## Display

**Orientation:** Portrait (320×480).

**Driver:** Start from the ST7789 example in [`docs/waveshare-examples-repo/examples/MicroPython/01_GUI/ST7789.py`](../docs/waveshare-examples-repo/examples/MicroPython/01_GUI/ST7789.py). That file handles init, window selection, single pixel, single square, full-screen fill - enough to boot the screen but not enough for a UI. Extend it with at least `fill_rect(x, y, w, h, color)` and `blit(x, y, w, h, buffer)` so glyphs and the QR matrix can be pushed as rectangular blocks.

**No power-on static:** On power-up the ST7789's internal RAM holds uninitialized garbage. The driver therefore keeps the backlight **off** until after `lcd_init()` *and* a full-screen solid-gray fill have run, then enables the backlight. The device never shows random pixels - it powers on to a solid gray screen (gray, not black, so it is obviously on). The boot flow then shows a brief (~1.2 s) splash: a big gray "OTP" stacked vertically, drawn purely with `fill_rect` block letters (no image assets), then the first real screen. The splash is HAL-agnostic so it renders identically in the simulator.

**Rendering strategy:** Direct-to-SPI. Each draw call pushes pixels straight over SPI; no RAM framebuffer. Pros: tiny memory footprint (a full 320×480×16-bit framebuffer would be ~307 KB out of 520 KB SRAM). Cons: visible flicker on large redraws.

If flicker turns out to be bad in practice, upgrade to a **partial framebuffer**: allocate a small RAM buffer for the region being redrawn (e.g. one keyboard row or the QR area), draw into it, then blit it to the screen in one SPI transfer. No full-screen framebuffer is ever allocated.

**Text input partial updates:** Screens with a text input field must use character-level partial updates - never clear the whole input area on every keypress. The pattern (used in `SendScreen` and `AddContact`):
- **Fast path** (+-1 char, no line reflow): repaint only the 1-2 character slots that changed. The font is monospace so slot positions are `x = left_margin + index * font.max_width()`. For append: draw the new char (and cursor if shown) at their slots - no clearing needed since `draw_text` fills its own background. For delete: clear exactly one vacated slot with `fill_rect`.
- **Slow path** (initial draw, line wrap/unroll, or any other case): compare new vs previously rendered lines and repaint only lines that differ - never the whole area.
- Track rendered state in `prev_lines` / `prev_display` locals in the screen's `draw_input` closure. Reset these when `draw_chrome` wipes the screen.

**Widget layer:** Small custom layer (button, label, keyboard) built on top of the driver primitives. No LVGL or external UI framework, to keep the dependency footprint minimal.

## Fonts

**Tool:** [Peter Hinch's `font_to_py`](https://github.com/peterhinch/micropython-font-to-py) - converts a TTF/BDF to a `.py` file with all glyphs packed as bytes. Run once per size; output files are committed to the repo.

**Font:** DejaVu Sans Mono (monospace - simplifies keyboard layout and hex alignment).

**Two sizes:**
- `font_14.py` - ~14 px tall, used for messages, hex output, labels
- `font_28.py` - ~28 px tall, used for keyboard keys and headings

Both files live in `firmware/core/fonts/`. The widget layer has one primitive, `draw_text(fb, x, y, text, font, color)`, that looks up each glyph and blits pixels into the framebuffer. Works identically in the simulator and on hardware - no HAL dependency.

## QR Code Generation

**Library:** `uQR` - a single pure-Python `.py` file dropped onto the board. Gives a 2D list of booleans (True = black module). The display layer iterates the matrix and draws filled squares onto the framebuffer.

`adafruit_miniqr` was considered but only supports QR versions 1–9, which cannot encode any of our payloads (see `scripts/qr_size_check_results.md`). `uQR` has no such cap.

**Message length cap: 500 bytes of plaintext.** On-board uQR produces a QR v22 (105 modules, ~3.05 px/module on the 320 px screen) - above the 3.0 px/module readability threshold. 600 bytes would drop below 3.0 px/module. One QR frame covers the entire message; no multi-frame scheme. The UI enforces this cap with a character counter during text entry.

**uQR MicroPython patches:** Two changes are needed to make uQR work under MicroPython (see bring-up log item 10 for details):

1. `QRData.write()` for `MODE_ALPHA_NUM` must use 1-byte slices (`chars[0:1]`) instead of integer indexing (`chars[0]`), because MicroPython's `bytes.find()` does not accept `int` arguments. The vendored `firmware/core/vendor/uQR.py` already has this patch applied.
2. Callers must pass `optimize=0` to `qr.add_data()` so uQR's mode auto-detection runs through `optimal_mode()` (a plain membership check) instead of the regex-based `optimal_data_chunks` (which misbehaves under MicroPython's stripped-down `re`). Without this, uQR falls back to `MODE_8BIT_BYTE` and the QR version balloons.

### QR Sizing Spike

Before the cap is frozen in firmware, a one-off measurement script determines the largest plaintext that still produces a QR readable on the 320 px screen. No hardware needed - runs on a laptop in a venv.

**Script:** `scripts/qr_size_check.py` (throwaway, committed so results are reproducible).

**What it does:**

1. For a range of candidate plaintext sizes (200–750 bytes), it builds a realistic frame: `[offset (4)] [length (2)] [ciphertext (N)] [tag (8)]`, hex-encodes it uppercase, and feeds the resulting string to the `qrcode` PyPI library (desktop stand-in for `uQR`).
2. Prints a table with: plaintext size, hex-string length, QR version chosen, module count (N×N), and px-per-module on a 320 px screen (`320 // N`).

**Note:** `uQR` itself is MicroPython-only and was not run on the laptop. Desktop results use `qrcode`, which produces equivalent output. Correctness of `uQR` specifically is verified in bring-up checklist item 10.

**Decision rule:** target **≥3 px per module** on the 320 px screen. Below that, the GM861XS scanner starts struggling at normal hold distance. The largest plaintext size that still hits that threshold (with headroom for ECC level M) becomes the firmware cap.

**Output of the spike:**

- The chosen plaintext cap (written back into this README replacing "TBD").
- Confirmation that `uQR` is the chosen library (already decided - see bring-up checklist item 10 for on-device verification).
- The picked ECC level.
- A short note committed to `scripts/qr_size_check_results.md` so the numbers are auditable later.

## SD Card Driver

**Library:** patched copy of MicroPython's standard `sdcard.py` (the same file Waveshare ships in `examples/MicroPython/02_SD/sdcard.py`). Lives at `docs/waveshare-examples-repo/examples/MicroPython/02_SD/sdcard.py` in this repo with the patches below applied; `real.py` ships this file alongside the firmware.

Wired to the onboard TF slot via SPI1 (`sck=GPIO26, mosi=GPIO27, miso=GPIO28, cs=GPIO31`).

**Patches applied** (see bring-up log item 11 for full debugging trail):

1. **CRC stop-bit fix.** Every SD command frame's last byte must end with bit 0 = 1 (the stop bit). The shipped driver passes `crc=0` (LSB = 0) for every command except CMD0/CMD8. Most cards tolerate this; the Intenso 32 GB SDHC (and other strict cards) silently refuse to respond. All `crc=0` arguments were changed to `crc=0x01` (CRC7 = 0, stop bit set), which keeps CRC checking off while making the frame format-legal.
2. **`_CMD_TIMEOUT` bumped from 100 → 1000.** Some 32 GB cards need >100 polls of ACMD41 before reporting ready (MicroPython issue #7129). Belt-and-suspenders.
3. **Bounded post-write busy-waits.** The stock driver's `write()` and `write_token()` end in unbounded `while spi.read(1)[0] == 0: pass` loops that poll MISO until the card signals "write done". If the card is hot-pulled during a write, MISO floats / reads 0 forever and the MCU spins in that loop - touch dies, the power-button poll dies (also I2C-blocked behind the spinning loop), and only a 5 s long-press recovers. Both loops are now bounded by a **500 ms wall-clock deadline** (`time.ticks_ms`) and raise `OSError(5)` (EIO) on timeout, so a yanked card surfaces as a normal exception instead of a freeze. 500 ms is above the SD spec's worst-case programming time (~250 ms for SDHC), so a healthy but slow card is not falsely cut off; an earlier fixed-poll-count bound was too aggressive for the Intenso 32 GB SDHC and tripped on legitimate writes.

Without these patches, `sdcard.SDCard.__init__` raises `OSError: timeout waiting for v2 card` on this board with this card, and hot-pulling the card during any write hangs the MCU.

### Atomic write hygiene

All file writes (`write_file`, `write_secret`, `write_file_stream`, `write_secret_stream`) use a tmp+rename pattern: data goes to `<final>.tmp` first, then `os.rename` swaps it into place. Two failure modes can leave a stale `.tmp` behind:

1. **Exception mid-write** (e.g. SD read/write timeout, card pulled). The write handle's `__exit__` and the whole-file write helpers now **best-effort delete the `.tmp`** on any exception so the next attempt isn't blocked and FAT doesn't accumulate orphaned clusters. A `try: os.remove(tmp) except OSError: pass` around the cleanup keeps the original exception propagating.
2. **Hard reset mid-write** (watchdog reboot, battery yank, PWR long-press). The MCU is gone before `__exit__` runs, so the `.tmp` survives into the next boot. To self-heal: after `mount_card("own")` succeeds, `RealHAL` sweeps `/secret/` and `/exchange/` for any `*.tmp` files and removes them. The sweep is shallow (only those two directories - and `/secret/contacts/<id>/` for per-contact bookkeeping `.tmp`s) and runs once per mount, so the cost is a couple of directory listings.

Together these guarantee that no atomic write leaves persistent state behind, even across crashes, and FAT clusters cannot leak into permanent orphans the way they did on the Intenso card during early bring-up.

## Message Authentication

Each message carries an 8-byte HMAC-SHA256 tag. It replaces CRC16 - it catches both accidental corruption (scan errors, typos) and deliberate tampering, since forging a valid tag requires 8 secret pad bytes the attacker doesn't have.

**Algorithm:** HMAC-SHA256, truncated to 8 bytes. Applied over `offset || length || ciphertext` before hex-encoding.

**Key:** `pad_send[offset+length : offset+length+8]` on the sender side; `pad_receive[offset+length : offset+length+8]` on the receiver side. These bytes are consumed alongside the ciphertext - the watermark advances by `len(plaintext) + 8`.

On mismatch the UI shows "Message authentication failed" and does not decode.

## QR Scanner Configuration

Two scanner settings differ from factory defaults and are required for the firmware to work:

| Setting | Zone bit | Bits to set | Required value | Factory default |
|---|---|---|---|---|
| Read mode | `0x0000` | bits 1-0 | `01` (Command Triggered) | `00` (Manual) |
| Tail type | `0x0060` | bits 6-5 | `01` (CRLF) | `00` (CR) |
| Allow tail | `0x0060` | bit 0 | `1` (on) | `1` (on) |

Source: GM861XS User Manual §10.6 zone-bit list (read mode) and §7.6 (tail). All other defaults (baud 9600, parity none, QR Code on, prefix/AIM/Code ID off) are already correct.

**Auto-config on boot:** the MCU configures the scanner over the same UART used for scanning, using the zone-bit protocol in the GM861XS user manual §10. No manual scanning of setup QR codes is needed. On every boot, after `qr_ping` confirms the scanner is alive:

1. Read zone `0x0000`. Mask in bits 1-0 = `01` to compute the target byte (preserving bits 7-2, which control LED, mute, and lighting).
2. Read zone `0x0060`. Mask bits 6-5 = `01` and bit 0 = `1` to compute the target byte.
3. For each zone whose current value differs from its target, write the target (zone-bit write command, type `0x08`).
4. If anything was written, send the "save to flash" command (`7E 00 09 01 00 00 00 DE C8`, 9 bytes, fixed CRC).
5. If both zones already match, do nothing - no flash writes.

A fresh-from-factory scanner self-configures on first boot. Settings persist in the scanner's internal flash, so step 4 normally runs only once per device. Subsequent boots pay two reads (~50 ms) and exit. If `qr_ping` fails (scanner unplugged or unreachable), auto-config is skipped and the UI shows the existing "QR scanner not detected" warning; next boot retries.

**Why command-triggered:** the firmware sends a trigger byte over UART when the user enters the scan screen; the scanner reads once, sends the result, then goes idle. Continuous mode would spam UART non-stop.

**Tail delimiter:** firmware reads UART bytes until `\r\n` to know the scan result is complete.

**Heartbeat:** at runtime, firmware pings the scanner with `7E 00 0A 01 00 00 00 30 1A` and expects `03 00 00 01 00 33 31` back (manual §2.1.2). If no response within timeout, QR scanning is disabled and the UI shows a warning.

**CRC:** zone-bit read/write commands use CRC-CCITT (poly `0x1021`, **initial value `0`**, no reflection, no xor-out) computed over the Types+Lens+Address+Datas bytes. The save and heartbeat commands have fixed CRCs documented in the manual, so only the runtime reads/writes need an on-board CRC computation.

**Frame formats** (manual §10.2, §10.3):
- Read: `7E 00 07 01 [ADDR_H] [ADDR_L] 01 [CRC_H] [CRC_L]` (9 bytes; trailing `01` = number of zones)
- Read response: `02 00 00 01 [VALUE] [CRC_H] [CRC_L]` (7 bytes)
- Write: `7E 00 08 01 [ADDR_H] [ADDR_L] [VALUE] [CRC_H] [CRC_L]` (9 bytes)
- Write response: `02 00 00 01 00 33 31` (7 bytes, fixed)

## Watchdog

A hardware watchdog (`machine.WDT(timeout=8000)`) is wired into `RealHAL` and is meant to be fed at least once every 8 seconds. Every UI loop that polls touch / power button (Home, PIN entry, Send, Receive, Contacts, Exchange progress, all "tap to continue" wait screens, etc.) calls `hal.feed_watchdog()` once per iteration, right next to the existing `hal.power_button_pressed()` / `session.is_idle_expired()` checks. The long-running streaming inner loops in `exchange.py` already check power button + idle on a chunk-modulo schedule; the watchdog feed is added to the same schedule (not every chunk, to keep overhead negligible).

This is a backstop, not a primary defense: any loop that fails to feed it is treated as a bug to be fixed. The watchdog exists so that an unanticipated hang produces an 8 s automatic reboot instead of the user having to hold PWR for ~5 s to trigger the AXP2101 hardware long-press shutdown (still available as the absolute last resort, per the root README's "Physical Controls" section).

`SimHAL.feed_watchdog()` is a no-op - there is no equivalent hardware watchdog on the laptop, and a soft-hang in sim is preferred to be investigated manually.

Enabled per device via `/flash/watchdog.txt`. Toggle with `scripts/inspect.sh <label> watchdog-on|off`. Production builds must ship with the file present on `/flash/`. When debugging with REPL open, use `watchdog-off` so the WDT does not reboot the board while you are inspecting state.

## Debug Tooling

Post-freeze debugging uses three pieces: a **breadcrumb** (last-known state), **touch injection** (host-driven synthetic taps), and an **inspect script** (`scripts/inspect.sh`).

**Agent-friendly:** with the gadget plugged in via USB-C, an AI assistant can drive the full debug loop on its own - flash, enable debug mode, inject touches, read post-freeze state - using `scripts/inspect.sh list` + `scripts/flash.sh --label <name>` + the `inspect.sh` verbs. See `scripts/README.md` for the workflow.

### Breadcrumb (`/flash/last_state.txt`, `/flash/breadcrumb_ring.txt`)

`firmware/core/breadcrumb.py` records every screen and phase transition in two files:

- `/flash/last_state.txt` is **overwritten** on every `mark()` with a single line. Used by `inspect.sh state` for the "live state" responsiveness check.
- `/flash/breadcrumb_ring.txt` is **appended** on every `mark()`. Rotated to `.prev` when it exceeds ~4 KB. Captures the trail of recent transitions so a crash site is not overwritten by post-reboot screens.

Both files share the same line format:

```
<unix_seconds> <ticks_ms> <free_ram_bytes> <label>
```

Example:
```
1746998312 1234567 387424 PrepareExchange.write_X_own@4194304/10485760
```

Label convention: `<Screen>.<phase>` plus optional `@<progress>/<total>` for long-running operations. Streaming loops (exchange.py) update the label on the same modulo schedule used for progress redraws - flash writes are bounded to ~20 per phase, not per chunk.

A third file, `/flash/reboot_history.txt`, is **appended** once per boot with one line: `<unix_seconds> <reset_cause_int> <reset_cause_name>`. Rotated to `.prev` when it exceeds ~4 KB.

### Touch injection

Gated by `/flash/debug_mode.txt`. When absent (production), `get_touch()` is unchanged. When present, `get_touch()` checks `/flash/inject_queue.txt` first: one `x,y` per line; the top entry is consumed and returned as a synthetic touch.

File-based injection was chosen over `mpremote exec`-based injection because exec sends Ctrl-C to acquire a REPL prompt, which raises `KeyboardInterrupt` in the running main loop - unacceptable during in-flight operations. File polling has no such side-effect; the per-touch cost is one flash stat call (no read unless entries are present). `debug_mode` is read once at boot into `self._debug_mode: bool` so the production overhead is a single boolean check.

### Flags

| File | Meaning |
|---|---|
| `/flash/debug_mode.txt` | Enable touch injection via file queue. Absent = production. |
| `/flash/watchdog.txt` | Enable hardware WDT (8 s). Absent = WDT off. |

Both flags are toggled via `scripts/inspect.sh <label> enable-debug|disable-debug` and `watchdog-on|off`, which write/delete the file and reboot the board.

### Production vs debug

Production: both files present (WDT on, touch injection off). Debug session: WDT on, debug mode on (inject taps without disabling the WDT). Temporarily disable WDT only when holding REPL open to inspect a freeze.

See [`scripts/inspect.sh`](../scripts/inspect.sh) for the full set of verbs.

## Hardware Detection on Boot

On startup, the firmware pings each external module and reports any that are unreachable:

- **QR scanner:** sends a ping command over UART and waits for a response. If no response within timeout → UI shows "QR scanner not detected". Scanning is disabled but all other flows work.
- **Guest SD slot:** a disconnected module is indistinguishable from "no card inserted" in software - both fail to mount the same way. Reported as `NO_CARD`.
- **Own SD slot:** onboard and not physically disconnectable.

## SD Card Detection and Validation

Every time a card is mounted (own or guest), a validation step runs before any operation. It returns a status that the UI maps to a plain-language message or warning:

| Status | Meaning |
|---|---|
| `OK` | Card mounted, structure valid, pad material available |
| `EMPTY` | Card mounted but no pad data found |
| `EXHAUSTED` | Card mounted, correctly formatted, but all segments used |
| `FULL` | Card mounted but no space left to write new data |
| `BAD_STRUCTURE` | Card mounted but expected folders/files are missing or wrong |
| `CORRUPTED` | Pad file exists but is truncated or unreadable |
| `WRONG_FS` | Card not FAT32 (e.g. exFAT, NTFS) - fails to mount |
| `NO_CARD` | No card detected |

This logic lives in `core/` - no hardware dependency. The HAL provides the raw mount attempt; core interprets the result.

**UI behavior:**
- Own card missing/invalid on boot → persistent warning banner on every screen, most actions blocked
- Guest card missing/invalid when key exchange is started → error shown, flow blocked
- Any card removed mid-operation → alert shown, operation aborted safely

**Key exchange edge cases:**

| Condition | Shown to user |
|---|---|
| Guest card has no `/exchange/X_own.bin` | "Other party hasn't prepared exchange yet" |
| Guest card has < 10 MB free | "Guest card has no space for exchange" |
| OTP checksum mismatch when A finalizes | "Exchange data corrupted - redo exchange" |
| Incomplete exchange found on own card at boot | "Incomplete exchange found. Finalize or discard?" |
| Own card already has pad data (re-exchange) | Confirmation required: "This replaces existing OTP. Continue?" |

**Implementation note - streaming, not whole-blob:** The 10 MB OTP does not fit in RAM (RP2350 SRAM is ~520 KB). Core uses the streaming HAL methods (`read_file_stream` / `write_file_stream` for plaintext guest-card files; `read_secret_stream` / `write_secret_stream` for encrypted own-card pad files) and never materializes a full pad in memory:

- **B's side (generating):** loop over `X_A` in small chunks (e.g. 4 KB) via `read_file_stream`, generate a same-size chunk of `X_B` from the TRNG, XOR, append to `OTP.bin` via `write_file_stream`, feed the result into a running SHA-256. After the loop, write the final digest into `OTP.bin`'s header. A second streaming pass reads `OTP.bin` back in chunks and writes B's own `/secret/pad_send.bin` and `/secret/pad_receive.bin` via `write_secret_stream` (fresh IV per file; CTR encrypts on the way out).
- **A's side (finalizing):** streaming SHA-256 over `OTP.bin` via `read_file_stream` to verify the checksum, then a second chunked pass to split and encrypt into A's own `/secret/` via `write_secret_stream`.

The same streaming pattern applies if pad size is ever raised beyond 10 MB.

## SD Card Encryption

All files under `/secret/` are encrypted with **AES-256-CTR** using a two-key (KEK/DEK) design: a random **master key (DEK)** encrypts all data; a PIN-derived **wrapping key (KEK)** encrypts only the master key. Changing the PIN re-wraps the tiny master key blob - the pad files are never re-encrypted.

**Key hierarchy:**
```
KEK = PBKDF2_HMAC_SHA256(PIN + device_secret, salt=card_salt, iterations=TUNED)
DEK = 32 random bytes, generated once at card init
      stored as /secret/master_key.enc = AES-256-CTR(key=KEK, plaintext=DEK)
```

On every boot: enter PIN → derive KEK → decrypt `master_key.enc` → get DEK → use DEK for all `/secret/` reads and writes.

Changing the PIN: derive the new KEK, re-encrypt DEK, write new `master_key.enc`. Pad files untouched.

**AES library:** Uses MicroPython's built-in `cryptolib.aes`. CTR mode (mode 6) is **not available** in this MicroPython build - `cryptolib.aes(b'\x00'*32, 6, b'\x00'*16)` raises `ValueError: mode`. ECB mode (mode 1) is available and used to implement CTR manually: for each block, the keystream is `AES-ECB(DEK, IV + block_index)`, XORed with the ciphertext. See `firmware/setup/rp2350-bringup-log.md`.

**AES-CTR helper:** The manual ECB-CTR construction lives in `firmware/core/crypto/ctr.py`. It exposes `aes_ctr_xor(key, iv, data, block_index=0)` for single-shot encrypt/decrypt and `CTRStream(key, iv, start_block_index)` for incremental streaming. Both `master_key.py` (DEK wrap/unwrap) and `real.py` (`read_secret*`/`write_secret*`) use these helpers.

**Bulk ECB optimisation:** `CTRStream` uses a **bulk ECB** approach to avoid the 24 KB/s per-block bottleneck. Rather than calling `aes.encrypt()` once per 16-byte block (65 536 Python→C round-trips per MB), `update()` builds all counter blocks for the incoming chunk into a single bytearray and calls `self._cipher.encrypt(buf)` once. MicroPython's `cryptolib` C source loops over all blocks internally in one Python→C transition, yielding **111 KB/s** (4.6× improvement). The cipher object is cached on the `CTRStream` instance for the stream's lifetime. This makes the composite read+AES+write throughput SD-read-limited (83 KB/s) rather than AES-limited (24 KB/s). See `plans/exchange-speed-improvements.md` Fix 6 for full analysis.

**Iteration count:** MicroPython has no built-in PBKDF2, so it runs as a pure-Python loop around `hashlib.sha256`. Measured throughput on RP2350 (MicroPython v1.28.0) is **~603 iterations/sec**. Default = **1000 iterations** (~1.66 s). Stock desktop-scale values (100 000+) would make boot unusably slow.

**Iteration count is stored per card**, not hardcoded in firmware, at `/device/kdf_params.json` (plaintext - it carries no secret information). Written once when the card is initialized; read on every unlock. If a future firmware raises the default (e.g. faster RP2350 variant, or larger pad sizes needing more resistance), new cards pick up the new value while existing cards keep decrypting with their original value. Without this, bumping the iteration count would silently brick every card ever initialized.

Lower iteration counts are acceptable here because the password already includes `device_secret` (256 random bits from MCU flash). PBKDF2 stretching only matters in the "both device and card stolen" case; against a 4–6 digit PIN, even a few thousand iterations per guess makes offline brute force infeasible on small hardware.

- **device_secret:** stored in `/flash/device_secret.bin` in the MicroPython filesystem on MCU flash. Generated on first boot; never written to the SD card.
- **card_salt:** stored plaintext in `/device/card_salt.bin`. Different per card, so a stolen card is useless on a different device even with the PIN.

**Wrong-PIN detection (`verify.bin`):**
Because the cipher has no built-in way to say "wrong key," there must be a known plaintext to check against. On card init we write `/secret/verify.bin` containing a fixed magic header (e.g. `OTPG_V1`) followed by the `card_salt` echoed back - encrypted with the DEK. On every unlock:

1. Derive KEK from the entered PIN + device_secret + card_salt.
2. Decrypt `master_key.enc` to get the candidate DEK.
3. Decrypt `verify.bin` with the candidate DEK.
4. If the magic header and salt echo match → PIN correct; proceed.
5. If not → PIN (or device_secret) wrong; increment the attempt counter and reject without touching any other `/secret/` file.

This is also how the "Restore device_secret" flow knows whether the pasted backup is correct: run the same check; garbage plaintext means the user entered the wrong value.

**PIN entry and rate-limiting:**
- 4–6 digit PIN entered via touchscreen on every boot.
- After 5 consecutive wrong attempts, cooldown doubles with each further failure (10 s, 20 s, 40 s, …). No wipe.
- Attempt counter and `cooldown_until` (unix seconds from `hal.rtc_now()`) are persisted to MCU flash (alongside `device_secret`), **updated before verification**, not after. A power-cycle between attempts does not reset the counter, and the cooldown is enforced against wall-clock time - not boot-relative `ticks_ms` - so rebooting does not shortcut the wait.

**PIN recovery (? button, upper-right of PIN screen):**
- **Restore using device secret backup:** user provides their saved device_secret via QR scan or manual hex entry. The device compares the entered value against `/flash/device_secret.bin`. On match, the DEK is recovered from `recovery_token.enc` (see below), the user sets a new PIN, a new KEK is derived, and `master_key.enc` is re-written with the DEK re-wrapped under the new KEK. `recovery_token.enc` is also rewritten with a fresh IV (DEK and recovery key unchanged). Pad files, contacts, and all other `/secret/` files are untouched. The attempt counter and cooldown are cleared. As a sanity check, the recovered DEK is used to decrypt `verify.bin` before the new PIN is accepted; a mismatch aborts the restore.
- **Wipe card & start fresh:** after double-confirmation (user types `RESET` on the QWERTY keyboard), the device deletes `/secret/` and `/exchange/` from the own card and re-enters the CardInit flow. All pad data and contacts are permanently destroyed.

**`recovery_token.enc`:** a second wrapping of the DEK kept on the card so the restore flow can recover it without the old PIN.

```
recovery_key       = HMAC-SHA256(device_secret, b"RECOVERY")   ← deterministic from device_secret
recovery_token.enc = [IV (16 bytes)] [AES-256-CTR(recovery_key, DEK)]
```

Written at CardInit, rewritten on every Change PIN and on every successful Restore (always with a fresh IV). Security is unchanged: an attacker can only unwrap `recovery_token.enc` if they have `device_secret` from MCU flash, which already collapses the "device stolen alone" defense - the same threat that PIN+PBKDF2 was protecting against in the first place. In return, restore needs no PIN brute-force loop.

**Device setup flow** (runs once per device, when `/flash/device_secret.bin` is missing):
0. Firmware checks for `/flash/device_secret.bin`. Present → skip to card init check. Missing → device setup (continue below).
1. Device generates `device_secret` and writes it to `/flash/device_secret.bin`.
2. Device displays `device_secret` as a QR code and as hex. Two options are shown:
   - **"I've saved it"** - user types the last 8 hex characters back in; on match the screen dismisses.
   - **"Skip for now"** - proceeds immediately without confirmation (backup not verified).
   This screen is shown only once.
3. Proceed to card init check.

**Card init flow** (runs whenever a mounted own card has no `/secret/verify.bin` - covers both the first card and any later replacement card):
1. UI prompts: "Blank card detected. Initialize?" with `Initialize` / `Cancel`. Cancel returns to home with the card flagged unusable.
2. User sets their PIN (4–6 digits).
3. Device generates `card_salt` (32 random bytes) → `/device/card_salt.bin`.
4. Device generates a random DEK, derives the KEK from `PIN + device_secret + card_salt`, writes `/secret/master_key.enc`, writes `/secret/recovery_token.enc` (DEK wrapped under the device_secret-derived recovery key - see PIN recovery), writes `/device/kdf_params.json` with the tuned iteration count, and writes `/secret/verify.bin`.
5. Card now has no pad data - UI directs user to `Contacts` → `+ Add contact` to populate it.

**Normal boot:** if both `/flash/device_secret.bin` and `/secret/verify.bin` exist → go straight to PIN entry.

**Restore after reflash:** A "Restore device_secret" option in the setup menu accepts the QR or hex backup and rewrites the MCU flash file.

**Key flow:** Core derives the KEK via PBKDF2, decrypts `master_key.enc` to obtain the DEK, then calls `hal.unlock_secrets(dek)`. HAL stores the DEK in RAM and uses it for all subsequent `read_secret`/`write_secret` calls. `hal.lock_secrets()` zeroes it. The sim receives the same call with a deterministic DEK derived from a fixed test PIN - no special-casing needed.

**HAL contract:** `hal.read_secret(path)` and `hal.write_secret(path, data)` wrap all `/secret/` access with AES-256-CTR using the in-RAM DEK. On every write, a fresh 16-byte random IV is generated and prepended to the ciphertext - so the file on disk is `[IV (16 bytes)] [ciphertext]`. On read, the first 16 bytes are peeled off and used as the IV. This ensures every write uses a different keystream, even for files that are rewritten repeatedly (e.g. the watermark).

**Random access into encrypted pads:** Sending or receiving a message only needs a small slice (500 pad bytes + 8 MAC bytes) out of 5 MB `pad_send.bin` / `pad_receive.bin`. Decrypting the full file for every message is not acceptable. CTR mode supports arbitrary-block seek by construction: keystream block N is `AES(DEK, IV + N)`, independent of all other blocks. The HAL exposes `hal.read_secret_slice(path, offset, length) -> bytes` that seeks to the right block and decrypts only the needed range. Since native CTR is not available on the RP2350 build, the keystream is computed manually via AES-ECB: `keystream_block_N = AES-ECB(DEK, IV + N)`, then XORed with the ciphertext slice.

**Random access overwrite for pad scrubbing:** `hal.overwrite_secret_slice(path, offset, data)` updates a small decrypted range inside an encrypted secret file without rewriting the whole 5 MB pad. The HAL reads the file IV, computes the CTR keystream for the requested offset, XORs `data` with that keystream, and writes the resulting ciphertext bytes in place. This is used only for pad scrubbing after successful message authentication. It is a logical overwrite through the SD filesystem, not a forensic-secure physical erase guarantee on MicroSD.

## User Settings

User settings live in encrypted `/secret/settings.json`. Missing file means defaults.

```json
{"burn_after_reading": false, "burn_after_reading_help_seen": false}
```

**Burn after reading:** toggle in Settings, default off. When enabled, a newly decoded received message is displayed once, not added to `session.message_history`, and the used receive-pad range (`offset` through `offset + length + 8`) is scrubbed with random bytes after successful authentication. Replayed messages are not scrubbed again; if the original receive-pad bytes were already scrubbed, replay authentication fails normally.

Boolean settings are rendered as rows with the label on the left, a small `?` help button near the right edge, and a toggle/checkbox at the far right. Tapping the row toggle immediately writes the encrypted settings file. If no modal was shown, only the toggle area is repainted. Tapping `?` opens the standard green-outline modal with the setting name, a short explanation, and `Done`; when it closes, the Settings screen is redrawn because the modal covered the page. The first time `Burn after reading` is enabled, that help modal is shown before the setting is saved so the user sees the MicroSD deletion caveat.

## Bookkeeping File Format

Pad consumption state is stored per contact under `/secret/contacts/<id>/` on the own SD card. Both files are AES-256-CTR encrypted like everything else under `/secret/`.

**`/secret/contacts/<id>/pad_send_watermark.txt`** - plain ASCII integer, e.g. `1234567`. The next unused byte offset in this contact's send pad. Sending a message to this contact advances it by `len(plaintext) + 8` (ciphertext + MAC key).

**`/secret/contacts/<id>/pad_receive_used_ranges.json`** - JSON array of `[start, end]` pairs, e.g. `[[0, 120], [500, 680]]`. Each decoded message from this contact appends its range. Adjacent or overlapping ranges are merged on every write to keep the file small.

**`/secret/contacts.json`** - manifest of all contacts on this card (schema version, `in_flight` exchange marker, contact array `{id, name, created_at}`). See the SD Card Data Structure section in the root README for the full schema.

**Atomic write strategy:** all writes go to `<file>.tmp` first, then `os.rename()` replaces the original. `os.rename()` on FAT32 is a single directory-entry update - if power is lost mid-write, the original file is untouched.

**State mutation order:**
- **Send:** advance the watermark first, then render and show the QR code. If power is lost after the watermark advances but before the QR is shown, those pad bytes are wasted but never reused - safe.
- **Receive, Burn after reading off:** append the used range first, then display the plaintext and add it to in-RAM history. Same principle.
- **Receive, Burn after reading on:** after successful authentication, append the used range, scrub the used receive-pad bytes, then display the plaintext once without adding it to in-RAM history. If the scrub write fails, fail loudly and do not render the plaintext.

## Replay Handling

If a received message's `(offset, length)` range overlaps a range already in `used_ranges`, the device decodes and displays the message normally but shows a warning banner: *"This message has already been decoded."* The user sees the content; no new range is written. If Burn after reading was enabled when the message was first read, the scrubbed pad bytes normally make later replay authentication fail before this warning can be shown.

## Battery Level

The onboard AXP2101 power management chip exposes battery state over I2C. The HAL reads this and provides two values to core: `battery_percent` (0–100) and `is_charging` (bool). The UI displays a battery icon in the corner of every screen, with a charging indicator when plugged in.

In the simulator, battery level is set via a slider and the charger state via a toggle in the hardware panel.

## On-Screen Keyboard

Text input uses one of **three on-screen keyboards**, picked per screen by the kind of input expected. Smaller alphabets get bigger touch targets - important on a 320 px wide screen.

Two widgets back the three variants:

- `Keyboard` - full QWERTY (existing).
- `Keypad` - shared grid widget, parameterized by a layout config (rows × cols, key labels, function row). Used for the digit and hex variants.

All three sit at the bottom of the screen and emit the same callbacks (`on_char`, `on_backspace`, `on_done`) so screens are agnostic to which one they hosted.

**Press feedback:** Every key (character and function) shows a light-blue highlight while held. Only the single pressed key is repainted (not the whole keyboard) - a full repaint would be too slow on direct-to-SPI rendering. The highlight clears when the finger lifts, after the same 150 ms idle window used for hold-debounce.

Both widgets expose one entry point: `update(touch)` where `touch` is `(x, y)` or `None`. Hosting screens call `kb.update(t)` once per loop iteration regardless of touch state; the widget owns all timing (highlight, hold-debounce, layer state, backspace repeat).

### QWERTY keyboard

iPhone-style QWERTY with three layers:

- **Letters** (default): QWERTY, shift key toggles upper/lower case.
- **Numbers & basic punctuation** (`123` key): digits + common punctuation.
- **Symbols** (`#+=` key, reached from the numbers layer): remaining symbols.

Behavior matches iOS: auto-reset of shift after one uppercase letter, `123`/`ABC` layer toggle. Character keys: holding registers exactly once (no repeat). Backspace: holding fires once immediately, then after 400 ms fires repeatedly every 80 ms (standard typematic). Long-press for accented chars is not required for v1.

Used by: message body (Send), contact name (Add contact).

### Digit keypad

iPhone-style 3×3 digit grid plus a function row. Five rows total.

```
┌─────────┬─────────┬─────────┐
│    1    │    2    │    3    │
├─────────┼─────────┼─────────┤
│    4    │    5    │    6    │
├─────────┼─────────┼─────────┤
│    7    │    8    │    9    │
├─────────┼─────────┼─────────┤
│         │    0    │         │
├─────────┴┬────────┴────────┤
│    <     │       GO        │
└──────────┴─────────────────┘
```

Only digits are emitted; there is no way to type non-digit chars. Backspace removes the last digit; GO submits.

Used by: PIN entry (boot unlock), Set PIN (card init), Change PIN (settings).

### Hex keypad

4×4 grid covering `0–9 A–F` plus a function row. Five rows total.

```
┌───────┬───────┬───────┬───────┐
│   0   │   1   │   2   │   3   │
├───────┼───────┼───────┼───────┤
│   4   │   5   │   6   │   7   │
├───────┼───────┼───────┼───────┤
│   8   │   9   │   A   │   B   │
├───────┼───────┼───────┼───────┤
│   C   │   D   │   E   │   F   │
├───────┴┬──────┴───────┴───────┤
│   <    │         GO           │
└────────┴──────────────────────┘
```

Uppercase only (the wire format is uppercase hex - see "Wire Format" in the root README). No way to type non-hex characters.

Used by: device-secret backup confirm (DeviceSetup, last 8 hex chars), manual ciphertext entry (Receive).

### Sim parity

All three keyboards are pixels drawn by core widget code - same in sim and on board. No HTML, no DOM. Per the simulator's fidelity rule, anything inside the 320×480 box is painted by the same Python that runs on hardware.

## Simulator

A small Flask web app (`sim/`) renders a 320×480 touchscreen in the browser. Mouse clicks act as touch input. Runs the real `core/` logic on the backend via `sim.py`. Lets you develop and test the full UI and all flows without physical hardware.

**Layout:**

```
                    [ Power bar ]
[ Hardware panel ]  [ 320×480 screen ]  [ QR input panel ]
```

- **Left panel (hardware):** own SD card slot, guest SD card slot, charger toggle, battery % slider. Both slots have identical UI (see SD Card Slots below). The PIN is entered on the simulated screen exactly as on hardware - no bypass; the real PBKDF2 / `verify.bin` / `master_key.enc` path runs in sim.
- **Power bar (above the screen):** power state label + power on/off button (see Power on/off below).
- **Center:** the simulated touchscreen (portrait, 320×480)
- **Right panel:** QR scanner input field + submit button

Toggling an SD card simulates inserting or ejecting it mid-session. Toggling the charger updates the battery icon. Submitting a QR value injects it as if the physical scanner read it.

**Power on/off (sim-only):** Each instance starts **powered off**. While off, the power bar shows "Device powered off" with a "Power on" button, and the canvas is black - the core thread does not exist. Clicking "Power on" creates a fresh `SimHAL` and starts `main_loop` in a new daemon thread; the boot flow runs from the top. When on, the bar shows "Device powered on" with a "Power off" button; clicking it drops the HAL + thread reference (daemon thread exits naturally) and clears the canvas. Power state lives server-side in the instance registry and is shared across tabs of the same instance; a Flask restart returns every instance to off. No equivalent on real hardware - the physical PWR button cuts power directly. This simulation exists so RAM-clear-on-power-off behavior can be observed in sim. The power control lives in the sim layer (`firmware/sim/app.py`); no code in `core/` or `hal/` is aware of it.

**Fidelity rule:** everything inside the 320×480 box is pixels only, drawn by the exact same widget and font code that runs on the board. No HTML buttons, no CSS text, no DOM keyboard inside the screen area - if core calls `fill_rect` / `blit_rect`, that is all the browser receives. The keyboard, text, and QR matrix seen in the sim are all painted by Python code identical to what will run on hardware.

Panels outside the screen (SD toggles, PIN, QR input, battery slider) are plain HTML/JS - they simulate the physical world around the device, not the screen, so they have no hardware equivalent to preserve.

**Display transport:** a single `<canvas width=320 height=480>` in the center. `sim.py` pushes HAL draw ops over a WebSocket:

- `{op: "fill", x, y, w, h, color}` → `ctx.fillRect`
- `{op: "blit", x, y, w, h, data: <base64 RGB565>}` → decoded into `ImageData` and drawn with `putImageData`

Touch goes back on the same socket: canvas `pointerdown` → `{x, y}` → `hal.get_touch()` returns it to core. The hardware panel uses normal Flask routes to flip flags in `sim.py` (SD mount state, charger, battery %, injected QR strings).

**Crypto parity:** `sim.py` runs real AES-256-CTR against a local folder for `/secret/` - same `read_secret` / `write_secret` / `read_secret_slice` / `read_secret_stream` / `write_secret_stream` semantics as the board, including the per-write random IV and the seek-to-counter math used for random-access pad slicing and streaming. The full PBKDF2 / KEK / DEK / `verify.bin` path runs unchanged in sim - the user enters a real PIN on the simulated screen, and the exact same crypto calls fire as on hardware. This means the first time the seek logic and the unlock flow run for real is not on the board.

### Multi-Instance Hub

Key exchange in the real world involves two physical devices swapping one SD card. The sim models this with a **hub** - a single Flask process that runs any number of device instances side by side. All instances live in one process; there are no separate ports.

**Launch:**

```bash
python -m firmware.sim
```

Opens `http://localhost:8080` - the hub page.

**Hub UI:**
- Device type cards across the top (only "3.5-inch gadget" for now).
- Instance list below: each row shows the instance name, a power-state dot (green = on, gray = off), a "Connect" button that opens the device's sim UI in a new browser tab, a "Rename" button (inline edit, updates `instances.json`), and a "Delete" button (removes from registry; state dir on disk is preserved so it can be re-added or inspected manually).
- "New instance" button → enter a name (must be non-empty) → instance is created (powered off) and its tab opens automatically.

**Power state on the hub:** all instances start powered off after a Flask restart. The dot refreshes when the page loads; toggling power from an instance tab is reflected the next time the hub page is refreshed (no live push - keeps the hub trivial).

**Sim state lives under `sim_state/`:**
```
sim_state/
  instances.json        ← instance registry (auto-created; empty list if missing)
  cards.json            ← global SD-card registry (auto-created; empty list if missing)
  alice/
    mcu_flash/          ← simulated MCU flash (device_secret.bin, PIN state)
  bob/
    mcu_flash/
  cards/
    <card_id>/
      device/           ← simulated SD card (/device/, /exchange/, /secret/)
      exchange/
      secret/
```

SD cards are **global**, like instances - they exist independently of any device and can be mounted into any slot of any instance. Per-instance state holds only `mcu_flash/` (the MCU's internal flash, which is per-device by definition) plus the `slots` field in `instances.json` (which card is currently in own / guest, persisted across power cycles).

If `instances.json` or `cards.json` is missing (fresh checkout or wiped state), the hub opens empty. No migration from older layouts - wipe `sim_state/` and start fresh.

### SD Card Slots

Both slots (own and guest) have identical UI:

- **Empty slot:** single `Choose card` button. Clicking it opens the **card picker modal**.
- **Occupied slot:** card name + `Eject` button. Eject clears the slot; the card returns to the registry and is available to any other slot.

**Card picker modal** lists every card in `cards.json` as a row showing the card name, an "in use elsewhere" hint when in use by another instance/slot, and a trash icon. It has a `+ New card` button at the top.

- **Pick a card:** tap a row. Cards in use by another instance/slot, or already in this instance's other slot, are greyed and not selectable.
- **+ New card:** prompts for a name (non-empty, must be unique), creates a fresh entry in `cards.json` with an empty `cards/<id>/` folder, and returns to the picker. Card init (PIN, `verify.bin`, etc.) runs the first time the card is mounted as `own`.
- **Trash icon:** disabled (greyed) while the card is in use anywhere. Otherwise prompts a confirm ("This wipes the card permanently") and on confirm removes the entry from `cards.json` and deletes `cards/<id>/`.

**Safety rail:** a card is considered "in use" iff some instance currently has it in its `own` or `guest` slot in `instances.json`. The card-picker greys out cards in use by another instance/slot, and the trash icon is disabled while a card is in use anywhere. Slot assignments - and therefore in-use state - persist across power cycles and Flask restarts, mirroring real hardware where an SD card stays physically inserted regardless of device power. Inserting the same card into both slots of the same instance is also disallowed.

**Exchange walkthrough in sim:**

1. On Alice's tab: tap `Contacts` → `+ Add contact` → enter a name → `Prepare exchange` → writes `X_own.bin` into Alice's mounted own card.
2. On Alice's tab: click `Eject` on the own slot. The card is now free.
3. On Bob's tab: in the guest slot, click `Choose card` → pick Alice's card from the list.
4. Bob runs the full streaming exchange, writing `OTP.bin` into Alice's card.
5. On Bob's tab: click `Eject` on the guest slot.
6. On Alice's tab: in the own slot, click `Choose card` → pick her card again → Alice finalizes, verifies checksum, splits, encrypts into `/secret/`, wipes `/exchange/`.

**Website Try-it coupling:** `matrixmole.com/otp-gadget` uses this simulator's
screen names, button labels, card moves, and sim-only `notify_screen` signals
as tutorial checkpoints. If the gadget UI flow changes, update the website
Try-it README and `try-it.ts` in the same change.

## Tests

Unit tests cover `core/` logic only - no hardware needed since core is plain Python. At minimum: OTP encrypt/decrypt correctness, key consumption and tracking, HMAC-SHA256 tag generation and verification, per-contact bookkeeping read/write, pad-split convention across both sides of an exchange, edge cases. Multi-contact-specific: manifest commit/delete/uniqueness (case-insensitive, trimmed; `in_flight.name` also blocks duplicates), `in_flight` lifecycle (set on add-start, committed-and-cleared on finalize success, cleared without commit on discard), `reconcile_in_flight()` recovery cases (stale marker no staging no pads → cleared; valid pads kind="add" → committed; valid pads kind="reexchange" → cleared), receive trial-decrypt picks the right contact across 3+ candidates and skips contacts with missing pads. UI and hardware flows are not tested.

## Waveshare Board Examples

Official MicroPython examples from the Waveshare wiki are mirrored at [`docs/waveshare-examples-repo/`](../docs/waveshare-examples-repo/). Use these as reference for the display driver, touch, and onboard peripherals.

## Hardware Bring-up Checklist

Things the simulator cannot prove. Each item must be verified once on the real board before trusting the firmware end-to-end. Failing any of these likely means reworking the approach, so they are checked early rather than after the full app is built.

| # | Check | How | Result | Notes / What to do if it fails |
|---|---|---|---|---|
| 1 | `cryptolib.aes` is available in the installed MicroPython build | REPL: `import cryptolib` | PASS | |
| 2 | CTR mode works | REPL: `cryptolib.aes(b'\x00'*32, 6, b'\x00'*16)` returns a cipher object | FAIL | CTR (mode 6) not available - `ValueError: mode`. ECB (mode 1) confirmed available. Using manual ECB-based CTR. ECB accepts multi-block buffers in one call (`aes.encrypt(bytes_4096)` processes 256 blocks in C), exploited by `CTRStream` for 4.6× throughput (24 → 111 KB/s). |
| 3 | ECB-based CTR seek behaves correctly for random-access pad slicing | Encrypt a known buffer, then decrypt a mid-buffer slice by computing `AES-ECB(DEK, IV + block_index)` XORed with ciphertext | PASS | Roundtrip and slice both verified - see bring-up log |
| 4 | PBKDF2 iteration throughput on RP2350 | Time a pure-Python PBKDF2 loop; pick an iteration count that yields ~1–2 s | done | Measured 603 iters/sec. Default = **1000 iterations** (~1.66 s). Stored per card in `/device/kdf_params.json`. |
| 5 | Guest SD does not interfere with LCD (both on SPI0) | Confirm screen stays fast after `mount_card("guest")` | FIXED | LCD and guest SD both used `SPI(0)`. SDCard.init_card() calls `spi.init(baudrate=100000)` which slowed the LCD to 100 kHz (full-screen fill ~24 s). Fixed by switching guest SD to `SoftSPI` on the same pins - SoftSPI does not touch the hardware SPI0 peripheral. |
| 6 | Onboard TF on SPI1 (GPIO26–31) mounts cleanly while SoftSPI guest SD is in use | Mount both cards back-to-back under the HAL | pending (7.3b) | Previously a concern due to SPI0 sharing; now SPI1 (own) and SoftSPI (guest) are fully independent. **Gotcha discovered during bring-up:** the guest SD module itself must be a **3.3V-only board** (no onboard regulator). Modules sold for Arduino (5V input → onboard AMS1117 → 3.3V → SD card) brown out when their VCC is wired to the Waveshare 3V3 rail - CMD0/CMD8 succeed but ACMD41 init fails with `OSError: timeout waiting for v2 card` after ~50 s of retries. See `docs/parts-and-products/index.md` → "SD Card Modules" for the only compatible part. |
| 7 | QR scanner (GM861XS) heartbeat + auto-config over UART1 on GPIO4/5 | `hal.qr_ping()` returns `True`; after a power-cycle, reading zone `0x0000` shows bits 1-0 = `01` and zone `0x0060` shows bits 6-5 = `01` and bit 0 = `1` | pending (7.3b) | Check UART wiring, baud, TX/RX polarity. If zone reads come back garbled, verify CRC-CCITT init value is `0` (not `0xFFFF`). |
| 8 | Display (ST7789) + touch (FT6336) both init without I2C contention | Boot a screen, read touch events | PASS | Display flashes correctly; FT6336U chip ID = 0x64; taps registered. See bring-up log. |
| 9 | Free SRAM after boot is enough for the streaming exchange, QR matrix, and keyboard state at the same time | `gc.mem_free()` after the app has started | PASS | 429 KB free at peak load (well above the 100 KB threshold). See bring-up log. |
| 10 | `uQR` generates a correct v22 matrix for a 500-byte payload and fits within a reasonable time budget | Drop `uQR.py` onto the board; generate a QR from a realistic 1028-char hex string; verify module count and scan it with a phone | PASS | uQR picks v22 (105 modules, 3.05 px/module) - one version larger than desktop `qrcode`'s v21. Two MicroPython compat patches applied to uQR (see bring-up log). Generation ~10 s. |
| 11 | Writing 5 MB of encrypted pad data to the onboard TF slot completes in acceptable time | Benchmark a full `pad_send.bin` write at card init | PASS | Write 334 KB/s, read 72 KB/s on Intenso 32 GB SDHC. Required patching `sdcard.py` (CRC stop-bit issue) - see bring-up log. |
| 12 | `hmac` module is available in the installed MicroPython build | REPL: `import hmac` | FAIL | Not available - `ImportError`. Writing a small wrapper around `hashlib.sha256` using the standard HMAC construction. |
| 13 | VBUS power-on suppression works: plugging in USB-C keeps the device off while charging | Power off the device, plug in a USB-C charger without pressing PWR; verify the screen stays dark and the device does not boot. Then press PWR while plugged in and verify normal boot. | pending | `RealHAL.__init__` reads `PWRON_STATUS` (reg 0x20) immediately after AXP init; if bit 2 (`vbus_pwron_stat`) is set and bit 0 (`btn_pwron_stat`) is clear, it calls `axp.power_off()` before any other init. The AXP2101 datasheet marks this VBUS power-on behavior as factory-customizable (EFUSE), so the actual default on the Waveshare board must be confirmed. If the bit is never set (VBUS power-on not wired on this board), the check is a harmless no-op. |

Results go in a short bring-up log (commit message or a file under `firmware/setup/`) so the next person building a device doesn't have to rediscover them.

## Crypto Pre-Spike (ESP32)

The ESP32 pre-spike was planned to de-risk the CTR design before the Waveshare board arrived, but was skipped - the board arrived first and the RP2350 bring-up supersedes it.

**RP2350 findings (MicroPython v1.28.0):**
- `import cryptolib` - available (PASS)
- CTR mode (mode 6) - not available (`ValueError: mode`) (FAIL)
- ECB mode (mode 1) - available (PASS)
- `import hmac` - not available (FAIL)

**Decision:** AES-CTR is implemented manually via AES-ECB keystream generation. HMAC is implemented as a small wrapper around `hashlib.sha256`. No different MicroPython build is needed.

See `firmware/setup/rp2350-bringup-log.md` for full details.

## Development Steps

All logic is developed and tested in the simulator first. Only after the simulator is working end-to-end is the firmware uploaded to hardware.

1. ~~ESP32 crypto pre-spike~~ - skipped; the Waveshare board arrived first. Crypto findings live in `setup/rp2350-bringup-log.md`.
2. Build simulator - implement `sim.py` HAL + Flask UI with hardware panel
3. Implement and test all `core/` logic against the simulator
4. Flash MicroPython `.uf2` onto the board (drag-and-drop via USB)
5. Deploy firmware with `scripts/flash.sh` and do a quick hardware smoke test - screen on, SD mounts, scanner responds to ping
6. Fix any wiring/hardware issues found in smoke test
