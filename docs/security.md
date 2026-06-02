# Security

How the OTP gadget stores key material, encrypts at rest, sources entropy, and defends against (and concedes to) realistic threats. For the protocol design, see [`protocol.md`](protocol.md).

## SD card data structure

The SD card is divided into three regions:

```
/device/     plaintext — card identity; used in encryption key derivation
/exchange/   plaintext — temporary staging area for key exchange; wiped after each exchange
/secret/     encrypted — all OTP material and bookkeeping
```

**`/device/`:**
- `card_salt.bin` — 32 random bytes, written once at first boot, never changed.
- `kdf_params.json` — PBKDF2 iteration count used when this card was initialized. Stored per card so the value can be raised in future firmware versions (e.g. when increasing pad size, or when the RP2350 gets faster) without breaking old cards.
- `version.txt` — format version (e.g. `v1`).

**`/exchange/`:** Exists only during a key exchange. Contains only pre-exchange random contributions and the intermediate XOR result — never finalized OTP data tied to any existing messages.

**`/secret/` (AES-256-CTR encrypted):**
```
master_key.enc                                     ← DEK (random 32-byte master key) wrapped with PIN-derived KEK
recovery_token.enc                                 ← DEK wrapped with device_secret-derived recovery key (PIN-recovery path)
verify.bin                                         ← known-plaintext token, used to detect wrong PIN/device_secret
contacts.json                                      ← manifest: schema version, in-flight exchange marker, contact list
settings.json                                      ← user settings, currently burn_after_reading
contacts/<id>/pad_send.bin                         ← per-contact: OTP bytes consumed when sending
contacts/<id>/pad_receive.bin                      ← per-contact: OTP bytes consumed when receiving
contacts/<id>/pad_send_watermark.txt               ← per-contact: offset of next unused send byte
contacts/<id>/pad_receive_used_ranges.json         ← per-contact: list of consumed receive ranges
```

**`contacts.json`** is the source of truth for which contacts exist. Each contact has a stable random 8-char hex `id` (used in paths and code) and a display `name` (shown in UI; unique case-insensitively + trimmed). The manifest also holds an `in_flight` field — `null` when idle, or a struct `{id, name, started_at, kind}` while an exchange is being prepared/finalized. Contacts only enter the contact list on successful finalize; pending adds live only in `in_flight`. `kind` is `"add"` (new contact) or `"reexchange"` (existing contact replacing its pads).

**`verify.bin`** is written once at card init with a known plaintext (a fixed magic string plus the `card_salt` echoed back). On every unlock the device decrypts it and compares against the expected value. If it doesn't match, the PIN (or `device_secret`) is wrong — the rate-limiter counter increments and nothing else in `/secret/` is touched. Without this token, wrong PINs silently return garbage from the pad files and the rate-limiter has nothing to trigger on.

Separate send and receive pads ensure no key material is ever reused between directions.

Messages are capped at **500 bytes of plaintext** — on-board uQR produces QR v22 (~3.05 px/module on the 320 px screen, above the 3.0 px/module readability threshold). Pad bytes are consumed exactly `len(plaintext) + 8` bytes at a time (the extra 8 are the per-message MAC key — see [Message authentication](protocol.md#message-authentication)).

**Bookkeeping:**

- `pad_send_watermark.txt`: a single integer — the offset of the next unused byte. Sending advances it by `len(plaintext) + 8`.
- `pad_receive_used_ranges.json`: a sparse list of `[start, end]` pairs. Each received message appends its range; adjacent ranges are merged on every write.

This supports lost and out-of-order messages: each ciphertext carries its own `(offset, length)` header (see [Wire format](protocol.md#wire-format)), so the receiver decodes independently of arrival order.

Decoding a received message does not write any plaintext to persistent storage — only the used range is recorded. If `Burn after reading` is enabled, the used receive-pad bytes are also overwritten through the encrypted pad file before the plaintext screen is shown.

**Pad scrubbing:** The gadget can scrub used pad bytes at the logical file level, so normal firmware reads can no longer recover them. This is not a forensic-secure MicroSD erase guarantee: SD card wear-leveling may leave old physical flash cells behind outside firmware control.

## SD card encryption

All data in `/secret/` is encrypted with **AES-256-CTR** using a two-key design:

```
KEK = PBKDF2(PIN || device_secret, salt=card_salt)   ← derived on every boot
DEK = random 32 bytes, generated once at card init    ← stored in /secret/master_key.enc
```

- **KEK (Key Encryption Key)** — derived from the PIN + device_secret + card_salt. Its only job is to wrap the DEK. Changing the PIN re-derives the KEK and re-encrypts the tiny `master_key.enc` blob — pad files are never touched.
- **DEK (Data Encryption Key)** — a random 32-byte master key that actually encrypts all other `/secret/` files. Lives in RAM for the session; zeroed on lock.
- **PIN** — 4–6 digits entered by the user on every boot. Rate-limited: 5 wrong attempts triggers a doubling cooldown before the next attempt is allowed.
- **device_secret** — 256 random bits generated once on first setup, stored in the MCU's internal flash. Never written to the SD card.
- **card_salt** — 32 random bytes stored plaintext in `/device/card_salt.bin`. Prevents precomputed dictionary attacks and means a stolen card from one device is useless on another.

The PBKDF2 iteration count is tuned per device at bring-up and stored per card in `/device/kdf_params.json`, so a future firmware can raise it for new cards without making existing cards unreadable.

This means:
- SD card stolen alone → unreadable without the device_secret.
- Device stolen alone → PIN rate-limiting prevents brute force.
- Both stolen → attacker must brute-force the PIN, significantly slowed by PBKDF2.

**device_secret backup:** At first setup, the device displays the device_secret as a QR code (and hex). The user stores it offline (e.g. written on paper, kept in a safe). If the device is reflashed or dies, restoring the device_secret on a new device recovers full SD card access. Without this backup, a lost or reflashed device means the SD card contents are permanently unreadable.

**Warning:** Dragging a new `.uf2` firmware file onto the board wipes MCU flash and destroys the device_secret. After initial setup, always update firmware using `scripts/flash.sh` — never reflash the `.uf2` unless you have the device_secret backup.

**Auto-lock:** After **5 minutes of no touch input**, the device auto-locks:

- DEK is zeroed from RAM.
- In-memory message history is cleared.
- UI returns to the PIN entry screen.

This limits exposure if the device is left unattended while unlocked. Unlocking again requires re-entering the PIN.

**Auto-power-off:** If the PIN screen sits idle for **10 minutes** with no touch (whether reached by auto-lock or by manual lock), the device calls the same `power_off()` path as a short PWR press — AXP2101 cuts system power and RAM is fully cleared. Combined with the 5-minute auto-lock, the device powers itself off after **15 minutes of total inactivity**. This prevents battery depletion if the device is forgotten in a bag or drawer.

**Power button:** A short press while the device is on powers it off immediately — the DEK is zeroed from RAM and the AXP2101 cuts system power. RAM is fully cleared on power-off. The next short press powers the device back on (AXP2101 handles this in hardware); it boots normally to PIN entry.

## True random number generator

For v1, entropy comes from the RP2350's built-in hardware TRNG.

This keeps the first version simpler and reduces hardware risk while still avoiding any dependence on external systems or networked devices.

A future version may add a separate avalanche-noise entropy source and XOR it with the RP2350 TRNG output for defense in depth.

## Possible weaknesses

- EM leakage.
- TRNG trust: v1 relies on the RP2350's built-in hardware TRNG.
- Cold boot attack: RAM retains data for seconds to minutes after power-off if chilled, potentially exposing in-memory message history. No RAM zeroing on shutdown is planned for v1.
- MicroSD forensic deletion: used pad bytes can be scrubbed through the filesystem, but the SD card controller may retain old physical flash cells because of wear-leveling.
- Guest card exposure during key exchange: B's device has read access to A's card while slotted, but A's OTP data is encrypted and inaccessible. Only A's `/exchange/` staging area (raw random bytes not tied to any messages) is readable.
- `device_secret` at rest: stored unencrypted in the MicroPython filesystem on MCU flash. Physical access with SWD/Picoprobe can read it, which collapses the "device stolen alone" defense — an attacker with both the device and the SD card can brute-force a 4–6 digit PIN offline. Hardware-level flash lockdown is deferred to a future version.

See also: [Related projects / prior art](related-projects.md) — how OTP Gadget's choices compare to other air-gapped messaging gadgets.
