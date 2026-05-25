# Parts overview

What is physically inside the gadget, at the chip-identity level. Intended for independent security review of the build.

This is not a buying guide. Supplier links, SKUs, prices, and "do NOT use" notes live in [`docs/order-checklist.md`](order-checklist.md).

## What is in the gadget

### Waveshare RP2350-Touch-LCD-3.5 (pre-assembled module)

A single off-the-shelf module bought as one unit and not modified. Bundles the MCU, display, touch controller, PMIC, and an onboard MicroSD slot.

- **MCU**: Raspberry Pi RP2350B. Dual Cortex-M33, 520 KB SRAM. Hardware TRNG on silicon - the source of all OTP key bytes. No software PRNG is used to generate key material.
- **Display**: 3.5 inch IPS LCD, 320x480. Display controller is **ST7789** (SPI, passive driver chip, no firmware, no radio).
- **Touch**: capacitive panel with **FT6336U** controller (I2C, passive).
- **PMIC / charger**: **AXP2101**. Battery charging, voltage regulation, on/off button. Controlled by I2C; otherwise a passive power-path chip.
- **Onboard MicroSD slot**: standard TF socket wired to the MCU via SPI. No chip on the socket - just contacts and a pinout.
- **RTC chip**: **PCF85063** is present on the module but **is not connected or used** in this build. The gadget works without wall-clock time. May be revisited if message timestamps become a feature.

### External components added by this design

- **QR scanner**: GM861XS UART module. TTL serial at 3.3V. Internally contains an image sensor and microcontroller running closed vendor firmware - it does image processing and outputs only the decoded text string over UART. It has no on-board storage, no display, and no radio. From the main MCU's point of view it is a one-way "type text" device.
- **Guest MicroSD breakout**: bare passive board with a card socket and a pinheader. No regulator, no level shifter, no IC of any kind. Pure passthrough at 3.3V.
- **Battery**: single-cell 3.7V lithium polymer, around 1800 mAh, with a 2-pin connector. Standard hobby chemistry.
- **USB-C connector**: built into the Waveshare module. Used for charging and, in development mode, for flashing firmware. Not used for normal operation.

## What is deliberately not in the gadget

This is the core trust claim. None of the following exist anywhere on the device:

- No wifi chip
- No Bluetooth chip
- No cellular modem (GSM, LTE, 5G)
- No LoRa or other long-range radio
- No GPS receiver
- No microphone
- No speaker
- No camera exposed to the main MCU (see the QR scanner note above for the trust boundary on its internal sensor)
- No external flash or EEPROM beyond the MicroSD cards the user inserts
- No second general-purpose MCU under the main MCU's control

If a device claims to be this gadget but contains any of the above, it is not this design.

## Notes for security review

- **Entropy**: OTP key bytes come from the RP2350's on-silicon TRNG. See `firmware/core/crypto/` and `firmware/hal/real.py` (`get_random_bytes`).
- **Egress**: in normal use, the only data path out of the device is the screen showing a QR code. The only data path in is the QR scanner reading a QR code.
- **Storage**: long-lived secret material on either MicroSD card lives under `/secret/` and is encrypted at rest (pads, contacts, settings, master key, verify token). KDF parameters under `/device/` (card salt, version marker) are plaintext - these are not secrets and have to be readable to derive the key. Material under `/exchange/` (raw OTP bytes during the key-exchange ceremony) is plaintext by design - the point of the exchange is to hand those bytes to the other person. Used pad bytes are overwritten with fresh random bytes after use (pad shredding). See `firmware/core/crypto/` and `firmware/hal/real.py` (`write_secret`, `overwrite_secret_slice`).
- **Trust boundary on the QR scanner**: the GM861XS runs closed vendor firmware. The trust assumption is that it does not exfiltrate (it has no radio and no way to). What it can do, in principle, is alter the decoded string before sending it over UART. This is the same risk as any external sensor with vendor firmware and is mitigated by the fact that all decoded messages are encrypted ciphertext that the scanner cannot meaningfully tamper with without breaking decryption on the recipient.
- **No real-time clock in use**: nothing in the firmware depends on wall-clock time. Anti-replay and pad-position tracking are byte-offset based, not time-based.

## Pointers

- Buying guide and supplier links: [`docs/order-checklist.md`](order-checklist.md).
- Wiring: `assembly/` (rendered diagram). Editable source files (`assembly/*.af`) are not shipped publicly.
- Case design: `case-design/README.md`, `case-design/slicer-settings.md`, `case-design/BEFORE-PRINT-CHECKLIST.md`. The printable case `.3mf` files themselves are a small paid Builder Pack on Gumroad; the rest of `case-design/` ships publicly.
- Firmware: `firmware/`.
