# User flows

What the device does, screen by screen — boot, PIN entry, Home, Contacts, Send/Receive, Settings, and how message history behaves in RAM.

On power-on the device shows a solid gray screen (so it is obviously on), then a brief gray "OTP" splash, then the boot flow — there is never any random-pixel static (see [`../firmware/README.md`](../firmware/README.md), Display section).

## Home screen

Shown on boot. Presents three main options: `Send message` / `Receive message` / `Contacts`. A small gear icon in the top-right corner opens `Settings`. A small lock icon in the top-left corner immediately locks the device (zeroes the DEK, clears in-RAM message history, returns to PIN entry) — equivalent to the 5-minute auto-lock but on demand. Per-contact message history lives inside each contact's thread under `Contacts` (no standalone History button).

## Contacts

The `Contacts` screen lists the device's communication partners. Key exchange lives here — setting up keys with someone = adding a contact. Multiple contacts per card are supported.

- **Contact names** are unique case-insensitively (trimmed). Enforced at add. Two "Martins" must be distinguished — e.g. "Martin S" / "Martin (work)" — so received messages can label the sender unambiguously.
- **Empty list:** message + `+ Add contact` button. Tapping it prompts for a contact name, then routes into the `Prepare exchange` flow (A-side) or `Finalize exchange` flow (B-side, when guest card has staging from the other party).
- **Populated list:** one row per contact, in `created_at` order (oldest first). Each row shows name + small subline `Send X.XX / 5.00 MB · Receive Y.YY / 5.00 MB` (remaining MB, truncated to 2 decimals so a single short message visibly drops the value below 5.00). Status badges appear if not Ready: `Waiting…`, `Ready to finalize`, `Re-exchange interrupted`.
- **In-flight pending row:** while an `Add contact` exchange is mid-flight (the contact isn't committed yet), a synthetic row for the pending name is prepended to the list with the appropriate status badge. Tapping it routes to the contact detail (Finalize / Discard exchange).
- **Tapping a Ready contact** opens the conversation thread — in-RAM messages exchanged with that contact this session, plus a `New message` action and a `…` menu (`Re-exchange`, `Delete contact`). Each message is shown in full: long messages wrap across multiple lines (no truncation). The thread paginates by line (not by message count), newest first; a message longer than one page continues on the next.
- **Tapping a non-Ready contact** (Waiting / Ready to finalize / Re-exchange interrupted / pending add) opens the contact detail directly with the appropriate action (`Finalize exchange`, `Restart re-exchange`, `Delete contact`, `Discard exchange`). No thread without ready keys.
- **One in-flight exchange at a time, globally.** While any exchange (add or re-exchange) is mid-flight, `+ Add contact` and `Re-exchange` are blocked with a message naming the contact whose exchange is in progress.
- **Delete contact** is offered from the contact detail. Wipes that contact's pad files only; other contacts are untouched.

## Settings

Opened via the gear icon, top-right of Home.

- `Change PIN`.
- `View device secret` — displays the device_secret as a QR code and hex string so it can be backed up at any time. No PIN re-entry needed (user is already authenticated).
- `Burn after reading` — toggle, default off. When on, a newly decoded received message is shown once, is not added to the in-RAM conversation thread, and the exact `pad_receive` bytes used for that message (`ciphertext + MAC key`) are scrubbed after successful authentication. This prevents someone who later gets the unlocked device from reopening that received message through the gadget UI. Boolean settings are shown as setting rows with a toggle (or checkbox if space requires it) and a small `?` help button. Tapping `?` opens the standard green-outline modal explaining the setting. The first time `Burn after reading` is enabled, the same explanation modal is shown automatically before the setting is saved. Later ON/OFF taps update only the toggle area, not the full Settings screen.

## PIN entry

Shown on every boot.

A digit keypad fills the lower portion of the screen. A `?` button (~36×36 px) sits in the upper-right corner. The same `?` button is also shown on the cooldown screen ("Too many wrong PINs. Please wait…") so recovery or wipe is always reachable regardless of lockout state. Tapping it opens the **PIN help modal** with two options:

- **Restore using device secret backup** — recovers access without losing any data. User provides their saved device_secret either by scanning its QR code or typing the hex manually. The device verifies the entered value matches what is stored in MCU flash. On match, the existing DEK is recovered from `recovery_token.enc` (wrapped under a key derived from the device_secret); the user sets a new PIN; the DEK is re-wrapped with the new PIN-derived KEK and `master_key.enc` is overwritten. Pad files and contacts are untouched. The attempt counter is reset.
- **Wipe card & start fresh** — destroys all pad data and contacts on the own card. After typing `RESET` on the keyboard, the user picks one of two options:
  - **Wipe card only** — deletes `/secret/` and `/exchange/` on the own card and routes into CardInit to set a new PIN. MCU flash (device_secret) is untouched.
  - **Full factory reset** — deletes `/secret/` and `/exchange/` on the own card AND wipes MCU flash (device_secret + PIN attempt state). A second confirmation screen is shown before the wipe. After reset the device reboots into DeviceSetup as if brand new. Use this to hand the device to someone else or when completely locked out with no backup.

## Sending a message

1. Select "Send a message" on the home screen.
2. Type the message using the capacitive touchscreen keyboard.
3. Press send — the device applies the one-time pad and displays the encoded ciphertext.
4. Transmit the ciphertext via either:
   - **QR code**: the ciphertext is shown as a QR code on screen, horizontally centered and sized so the modules are the largest integer pixel size that still fits the available area (smaller messages → larger QR); the recipient scans it with their device.
   - **Manual**: read the hex ciphertext off the screen and transmit it via any channel of your choice.

## Receiving a message

1. Select "Receive a message" on the home screen.
2. Enter the ciphertext via either:
   - **QR scan**: point the device's QR scanner at the sender's screen; the ciphertext is read automatically.
   - **Manual entry**: type the hex ciphertext using the touchscreen keyboard and press submit.
3. The device automatically determines which contact sent the message by trial-decrypting against each contact's pad and checking the HMAC tag — first match wins. If no contact matches, the message is rejected as inauthentic. The decoded plaintext is shown labelled `From: <contact name>`. The wire format itself carries no contact identifier, preserving the no-metadata-leak property.

## Message history

Decoded and sent messages are stored in RAM only — never on the SD card or any other persistent storage. This means:

- Message history is available for the duration of the session.
- On power-off, RAM clears automatically and no plaintext is left on the device.
- Retransmitting a sent message always consumes fresh pad bytes (a new encode from scratch). Pad bytes are cheap (500 out of ~5 million per message); no reuse-of-used-bytes optimization is implemented.

If `Burn after reading` is enabled, newly received messages are excluded from the in-RAM thread after the one-time plaintext screen is dismissed. Sent messages are still shown in the session thread; received replays are not burn-scrubbed again.

**Note:** Some MCUs have low-power retention modes that keep RAM alive. Sensitive RAM regions should be explicitly zeroed before entering sleep or shutdown.
