# ESP32-C6 Crypto Pre-Spike

> **SKIPPED.** The Waveshare RP2350 board arrived before this pre-spike was run. The same questions were answered directly on the real target - see `rp2350-bringup-log.md`. The rest of this document is kept for reference only.

Goal: confirm that MicroPython's `cryptolib.aes` supports **AES-256-CTR** and that we can **seek to an arbitrary counter block** (needed for `hal.read_secret_slice`). This de-risks the `/secret/` encryption design before we start building the simulator.

The Waveshare RP2350 board hasn't arrived, so we use the ESP32-C6 DevKitC-1 as a stand-in. Same `cryptolib` module, different MCU port. A pass here is a strong signal, not final proof - items 1–3 of the bring-up checklist are re-run on the Waveshare board later.

## What you need

- ESP32-C6 DevKitC-1 (already have)
- USB-C cable (data, not just charging)
- Mac with Thonny installed (https://thonny.org)

## Step 1 - Download MicroPython firmware

1. Go to https://micropython.org/download/ESP32_GENERIC_C6/
2. Download the latest stable `.bin` (as of writing: `ESP32_GENERIC_C6-20260406-v1.28.0.bin`)
3. Save it to `~/Downloads/` (exact location doesn't matter, just remember it)

## Step 2 - Connect the board

The board has **two USB-C ports**. Use the one labeled **"USB"** (the native USB Serial/JTAG port built into the ESP32-C6 chip). **Not** the one labeled "UART" - that one needs a CP210x driver.

Plug the USB-C cable into the **USB** port and into your Mac.

Verify the Mac sees it:

```bash
ls /dev/cu.usb*
```

You should see something like `/dev/cu.usbmodem101` or similar. Note the full path.

## Step 3 - Flash MicroPython

Thonny can flash firmware directly - no command line needed.

1. Open Thonny
2. Tools → Options → Interpreter
3. Interpreter dropdown: **MicroPython (ESP32)**
4. Port: the `/dev/cu.usbmodem…` from step 2
5. Click **"Install or update MicroPython (esptool)"** at the bottom
6. In the dialog:
   - Target port: your port
   - MicroPython family: **ESP32-C6**
   - Variant: **Espressif • ESP32-C6 / 4MB flash**
   - Version: latest stable (matches what you downloaded)
7. Click **Install**. Takes ~30 s.
8. Close the dialogs. Thonny should auto-connect to the board and show `>>>` in the bottom shell.

If the shell doesn't appear: Run → Stop/Restart backend. Or unplug/replug the USB cable and hit the shell's red stop button.

## Step 4 - Run the crypto tests

Paste this whole block into the Thonny REPL (the `>>>` shell at the bottom) and hit Enter:

```python
import cryptolib, os

# ---- Test 1 & 2: cryptolib + CTR mode available ----
print("TEST 1: import cryptolib ... OK")
try:
    c = cryptolib.aes(b'\x00'*32, 6, b'\x00'*16)  # mode 6 = CTR
    print("TEST 2: AES-256-CTR constructor ... OK")
except Exception as e:
    print("TEST 2: AES-256-CTR constructor ... FAIL:", e)
    raise SystemExit

# ---- Test 3: encrypt/decrypt roundtrip ----
key = os.urandom(32)
iv  = os.urandom(16)
pt  = b'A' * 64  # 4 blocks of 16 bytes

enc = cryptolib.aes(key, 6, iv)
ct  = enc.encrypt(pt)

dec = cryptolib.aes(key, 6, iv)
rt  = dec.decrypt(ct)
print("TEST 3: roundtrip ...", "OK" if rt == pt else "FAIL")

# ---- Test 4: seek-to-counter (the critical one) ----
# Goal: decrypt only ct[32:48] (block index 2) without touching blocks 0,1,3.
# For CTR, the IV fed into the cipher acts as the starting counter block.
# So to decrypt from block N, construct IV' = (iv_as_int + N) as 16 big-endian bytes.

def add_counter(iv_bytes, n):
    x = int.from_bytes(iv_bytes, 'big') + n
    return (x & ((1 << 128) - 1)).to_bytes(16, 'big')

try:
    seek_cipher = cryptolib.aes(key, 6, add_counter(iv, 2))
    slice_pt = seek_cipher.decrypt(ct[32:48])
    print("TEST 4 (native CTR seek) ...", "OK" if slice_pt == pt[32:48] else "FAIL")
except Exception as e:
    print("TEST 4 (native CTR seek) ... FAIL:", e)

# ---- Test 5: ECB fallback for seek (plan B if test 4 fails) ----
# Build keystream manually: ECB-encrypt the counter block, XOR with ciphertext slice.
try:
    ecb = cryptolib.aes(key, 1, )  # mode 1 = ECB
except TypeError:
    ecb = cryptolib.aes(key, 1)    # some builds accept no IV for ECB

ks_block = ecb.encrypt(add_counter(iv, 2))
slice_pt_fb = bytes(a ^ b for a, b in zip(ct[32:48], ks_block))
print("TEST 5 (ECB fallback seek) ...", "OK" if slice_pt_fb == pt[32:48] else "FAIL")

# ---- Test 6: hmac module availability (bonus - used for message auth) ----
try:
    import hmac
    print("TEST 6: import hmac ... OK")
except ImportError:
    print("TEST 6: import hmac ... FAIL (will need hashlib-based wrapper)")

print("DONE")
```

## Step 5 - What the results mean

| Result | Meaning |
|---|---|
| Tests 1–3 OK | `cryptolib` + CTR work. Core encryption design is safe. |
| Test 4 OK | Native CTR seek works. `read_secret_slice` is simple - just pass an adjusted IV. |
| Test 4 FAIL, Test 5 OK | We use the ECB fallback path for `read_secret_slice`. Still fine, just slightly more code. |
| Tests 1, 2, or 3 FAIL | Big deal. The `/secret/` design has to change. Stop and tell me. |
| Test 6 FAIL | Minor. We'll write a ~10-line HMAC wrapper around `hashlib.sha256`. |

## Step 6 - Record the results

Copy the **full console output** into `firmware/setup/esp32-crypto-prespike-results.md` (a template exists - just fill in the fields). Commit it.

That file is the source of truth for which code path `real.py` will take when we implement `read_secret_slice` later.
