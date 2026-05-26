# OTP Gadget

A handheld, air-gapped device for exchanging encrypted messages with a one-time pad. No radio, no network, no cloud. Two devices, one MicroSD card swap, then messages flow by QR code or by hand-typed hex.

## What this repo contains

- **`firmware/`** - MicroPython source running on the gadget.
- **`scripts/`** - flash, build, and dev utilities.
- **`docs/`** - parts overview and order checklist with supplier SKUs.
- **`case-design/`** - printable case `.3mf` files, slicer settings, and the before-print checklist.
- **`assembly/`** - wiring diagrams and assembly notes.

Everything needed to build the gadget end-to-end (source parts, assemble, flash, slice, print, use) is in this repo. Nothing is paywalled.

## Where to go next

| Want to... | Go to |
|---|---|
| Try it in a browser demo | [matrixmole.com/otp-gadget](https://matrixmole.com/otp-gadget) |
| Build one yourself | [Builder docs](https://matrixmole.com/otp-gadget/docs) |
| Reserve a ready-made device (when available) | [Product page](https://matrixmole.com/products/otp-gadget) |
| Read the firmware source | [`firmware/`](firmware/) |
| Support the project | [GitHub Sponsors](https://github.com/sponsors/matrix-mole) |

## How it works (short version)

Two devices generate random bytes during setup, XOR them on one card to produce a shared one-time pad, then split that pad so each side's send-bytes match the other side's receive-bytes. Messages are encoded as QR codes on a 3.5" screen and scanned by the receiver. All key material lives on a per-device MicroSD card, encrypted at rest with AES-256-CTR under a PIN-derived key. The hardware has no radio of any kind.

For the full protocol, threat model, and hardware design, see the [builder docs](https://matrixmole.com/otp-gadget/docs).

## Hardware

Off-the-shelf modules, no custom PCB: Waveshare RP2350-Touch-LCD-3.5 (3.5" capacitive touch + RP2350 microcontroller + onboard battery management + onboard TF slot), UART QR scanner, a 3.3 V SPI MicroSD breakout, and a 1.8 Ah LiPo battery. See [`docs/parts-overview.md`](docs/parts-overview.md) and [`docs/order-checklist.md`](docs/order-checklist.md).

## License

- **Firmware** (`firmware/`, `scripts/`, `main.py`): GPLv3 - [LICENSE-firmware](LICENSE-firmware).
- **Documentation and printable case CAD** (`README.md`, `docs/`, `case-design/**` including the `.3mf` files, `assembly/`): CC BY-SA 4.0 - [LICENSE-docs](LICENSE-docs).
- **Matrix Mole / OTP Gadget brand**: not licensed. Forks must not present themselves as the official product.

## Supporting the project

If this is useful to you, you can support the work via [GitHub Sponsors](https://github.com/sponsors/matrix-mole). All revenue goes back into parts, testing, and (eventually) EEA compliance for a built-unit run via the reservation model on the product page above.
