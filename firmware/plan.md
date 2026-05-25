# Firmware Implementation Plan

Execute tasks in order. Do one task at a time. When picking up work, read the **Context** section first, then the "Next task" (the first unchecked task below).

## Context - read these before starting any task

- [`../README.md`](../README.md) - project overview, hardware, OTP protocol, crypto design, UX flows. Source of truth for requirements.
- [`./README.md`](./README.md) - firmware architecture, HAL interface, screen flow, sim design, bring-up checklist. Source of truth for structure.
- [`../docs/parts-and-products/index.md`](../docs/parts-and-products/index.md) - parts list.
- `setup/rp2350-bringup-log.md` - RP2350 bring-up results (CTR not available, ECB fallback decided, hmac not available).

## Ground rules

- `core/` is pure Python, no hardware calls. All hardware goes through HAL.
- Any HAL method used by `core/` must exist in **both** `sim.py` and `real.py` with identical signatures (see `firmware/README.md` "HAL Interface").
- If a task would change behavior that is already described in `firmware/README.md` or the root `README.md`, update those docs first, confirm with the user, then code.
- Match existing style. No comments unless the *why* is non-obvious.
- Keep each task's scope tight. Don't bundle unrelated changes.

## Tasks

### Phase 0 - Pre-spike (blocked)

- [x] **0.1 ESP32 crypto pre-spike** - skipped (no USB-C data cable available).

### Phase 1 - Simulator skeleton

- [x] **1.1 Create package skeleton** - `firmware/core/__init__.py`, `firmware/hal/__init__.py`, `firmware/sim/__init__.py`, `firmware/tests/__init__.py`. Add a top-level `firmware/__init__.py` if needed to make imports work. No logic yet.
- [x] **1.2 Define HAL base class** - `firmware/hal/base.py` with the full interface from `firmware/README.md` "HAL Interface" as abstract methods (raise `NotImplementedError`). This is the contract both `sim.py` and `real.py` implement.
- [x] **1.3 Minimal Flask app** - `firmware/sim/app.py` serving a single HTML page with a 320×480 `<canvas>`, hardware panel placeholders (own/guest SD toggles, battery slider, charger toggle, QR input field), and a WebSocket. Start with `python -m firmware.sim --port 8080`. No drawing yet - just the scaffolding.
- [x] **1.4 Display transport** - WebSocket protocol from `firmware/README.md` "Display transport": `{op: "fill", ...}` and `{op: "blit", ...}`. Wire `sim.py` → browser so calling `hal.fill_rect` in Python paints on the canvas. Test: draw a red rectangle on app start.
- [x] **1.5 Touch transport** - canvas `pointerdown` → WebSocket → `hal.get_touch()` returns `(x, y)` once, then `None`. Test: click on canvas, Python prints coords.
- [x] **1.6 Sim state dir + flash** - `sim.py` reads/writes `mcu_flash/` and `own_card/` under a `--state-dir` arg. Implement `flash_read/write/exists`, `mount_card`, `read_file/write_file/delete_file/file_exists/free_space`. No crypto yet.

### Phase 2 - Widget + font layer

- [x] **2.1 Font generation** - run Peter Hinch's `font_to_py` on DejaVu Sans Mono at 14 px and 28 px. Commit `firmware/core/fonts/font_14.py` and `font_28.py`. Document the exact command in `firmware/core/fonts/README.md`.
- [x] **2.2 Draw text primitive** - `firmware/core/widgets/text.py` with `draw_text(hal, x, y, text, font, color)`. Blits each glyph via `hal.blit_rect`. Test in sim.
- [x] **2.3 Button + label widgets** - `firmware/core/widgets/button.py`, `label.py`. Button has `draw`, `hit_test(x, y)`. Label is static text. Test: render three buttons on canvas, click them, print which one was hit.
- [x] **2.4 On-screen keyboard** - `firmware/core/widgets/keyboard.py`, three layers (letters / numbers / symbols) per `firmware/README.md` "On-Screen Keyboard". Emits characters via callback.

### Phase 3 - Crypto in `sim.py`

- [x] **3.1 AES-256-CTR in sim** - use `pycryptodome` or `cryptography` in `sim.py`. Implement `read_secret`, `write_secret`, `read_secret_slice`, `read_secret_stream`, `write_secret_stream`, `unlock_secrets`, `lock_secrets`. Per-write fresh 16-byte IV prepended to file. Match the on-disk format described in `firmware/README.md` "HAL contract".
- [x] **3.2 PBKDF2 + KEK/DEK in `core/`** - `firmware/core/crypto/kek.py` and `firmware/core/crypto/master_key.py`. Pure Python; uses `hashlib` only. Matches the scheme in `firmware/README.md` "SD Card Encryption".
- [x] **3.3 TRNG** - `hal.get_random_bytes(n)` in `sim.py` wraps `os.urandom`.

### Phase 4 - Core flows

- [x] **4.1 Device setup flow** - generates `device_secret`, writes to flash, shows QR + hex, last-8-hex confirmation. Screen: `DeviceSetup`.
- [x] **4.2 Card init flow** - detects missing `/secret/verify.bin`, prompts PIN, generates `card_salt`, tunes PBKDF2 iterations (sim uses a fixed value; real tunes at bring-up), writes `master_key.enc` + `verify.bin` + `kdf_params.json`.
- [x] **4.3 PIN entry + unlock** - `PINEntry` screen. Rate-limiter (5 wrong → doubling cooldown). Attempt state persisted via `hal.flash_write`. Cooldown keyed on `hal.rtc_now()`.
- [x] **4.4 Home screen + auto-lock idle timer** - 5 min idle via `hal.ticks_ms`. On lock: `hal.lock_secrets()`, clear in-RAM message history, return to `PINEntry`.
- [x] **4.5 Message encoding** - `firmware/core/message.py`: encode/decode the wire format `[offset (4)] [length (2)] [ciphertext (N)] [tag (8)]`, hex-upper. Pure functions.
- [x] **4.6 Message authentication** - HMAC-SHA256 truncated to 8 bytes (`firmware/README.md` "Message Authentication"). If `hmac` is missing on board, the wrapper lives in `core/` so sim and real are identical.
- [x] **4.7 Bookkeeping** - `pad_send_watermark.txt` and `pad_receive_used_ranges.json` read/write + merge-on-write. Atomic `.tmp` + rename via `write_secret`.
- [x] **4.8 Send flow** - `Keyboard` → `ShowCiphertext` (QR + hex). Uses `uQR` (commit it at `firmware/core/vendor/uQR.py`).
- [x] **4.9 Receive flow** - `PickInputMethod` → QR scan or manual hex entry → `ShowPlaintext`. Replay warning if range overlaps.
- [x] **4.10 In-RAM message history** - cleared on auto-lock. Rendered in a history screen (pick minimal UI; don't over-design).
- [x] **4.11 Burn after reading setting** - add encrypted `/secret/settings.json` with default `burn_after_reading = false`, a Settings toggle/checkbox row with a `?` help modal and first-enable explanation, `hal.overwrite_secret_slice` in sim + real, receive-path pad scrubbing after successful authentication, and tests for default settings, toggling, history exclusion, and scrubbed replay failure.

### Phase 5 - Key exchange (two sims)

- [x] **5.1 Two-sim launcher** - `scripts/run_two_sims.sh` (ports 8080 + 8081, state dirs A + B). Guest-slot path field in hardware panel per `firmware/README.md` "Two-Device Setup".
- [x] **5.2 Lockfile safety rail** - `.mounted.lock` in the guest folder blocks the other sim from re-inserting while guest.
- [x] **5.3 Prepare exchange** - A writes `X_own.bin` via TRNG streaming.
- [x] **5.4 Finalize exchange (B side)** - streaming chunked XOR, streaming SHA-256, write `OTP.bin` with header (magic/version/role/digest), then split + encrypt into B's `/secret/`.
- [x] **5.5 Finalize exchange (A side)** - verify checksum, split + encrypt into A's `/secret/`, wipe `/exchange/`.
- [x] **5.6 Exchange edge cases** - all conditions in `firmware/README.md` "Key exchange edge cases" table.

### Phase 6 - Tests

- [x] **6.1 Unit tests** - OTP encrypt/decrypt, HMAC generation + verification, wire format roundtrip, pad-split convention (A/B symmetry), bookkeeping merge, `verify.bin` unlock, rate-limiter cooldown math. Live under `firmware/tests/`.

### Phase 7 - Hardware

- [x] **7.1 Flash MicroPython UF2** onto the Waveshare RP2350-Touch-LCD-3.5.
- [ ] **7.2 Bring-up checklist items 1–12** from `firmware/README.md`. Record results under `firmware/setup/`. Items 1–4, 8, 9, 10, 11, 12 PASS - see `setup/rp2350-bringup-log.md`. Items 5–7 pending external hardware (guest SD breakout + GM861XS QR scanner not yet received).
- [x] **7.3a Implement testable parts of `real.py`** - all HAL methods exercisable without guest SD or QR scanner. Adds `firmware/core/crypto/ctr.py` (shared AES-CTR-via-ECB helper) and fixes `master_key.py` which used the unavailable CTR mode 6. Vendors drivers into `firmware/hal/drivers/`. Adds `firmware/main.py` as board entry point. Guest SD stubs return `NO_CARD`; QR scanner stubs return `False`/`None`. `read_secret_slice` and streams use the `CTRStream` helper.
- [ ] **7.3b Wire up guest SD + QR scanner in `real.py`** - replace stubs. Guest SD on SPI0 (GPIO3/6/32/33). GM861XS on UART1 (GPIO4/5). Requires external hardware (SD breakout, QR scanner, wiring). Covers bring-up checklist items 5–7. Code written 2026-05-01; awaits wiring for verification.
- [ ] **7.3c QR scanner auto-config in `real.py`** - on first successful `qr_ping` per boot, read GM861XS zone bits `0x0000` (read mode) and `0x0060` (tail). Read-modify-write: set zone `0x0000` bits 1-0 = `01` (Command Triggered) preserving the rest; set zone `0x0060` bits 6-5 = `01` (CRLF) and bit 0 = `1` (allow tail) preserving the rest. If anything changed, send the save-to-flash command. Adds CRC-CCITT helper (poly `0x1021`, **init `0`**, no reflection). Idempotent: no flash writes when scanner is already configured. Removes the need for manual setup-QR scanning during build. See `firmware/README.md` § QR Scanner Configuration.
- [ ] **7.4 Smoke test on board** - boot, PIN entry, home screen, send a test message, receive it on the second device.
- [ ] **7.5 End-to-end** - full key exchange on two real devices. Send + receive real messages.

## How to pick up the next task

1. Read the **Context** links above.
2. Find the first unchecked `[ ]` task.
3. If the task says to ask the user a question before proceeding, ask it and wait for the answer.
4. Do only that one task. Update the checkbox in this file when done.
5. If the task reveals something that contradicts the READMEs, stop and raise it with the user before coding.
