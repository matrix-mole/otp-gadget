# OTP Gadget

A handheld, air-gapped device for exchanging encrypted messages with a one-time pad. No radio, no network, no cloud. Two devices, one MicroSD card swap, then messages flow by QR code or by hand-typed hex.

## What this repo contains

- **`firmware/`** — MicroPython source running on the gadget.
- **`scripts/`** — flash, build, and dev utilities.
- **`docs/`** — parts overview and order checklist with supplier SKUs.
- **`case-design/`** — slicer settings + before-print checklist for the 3D-printed case.
- **`assembly/`** — wiring diagrams and assembly notes.

The printable case STL files (`.3mf`) are not in this repo — they're sold separately as a small Builder Pack to fund the project. Everything else needed to build the gadget is here.

## Where to go next

| Want to... | Go to |
|---|---|
| Try it in a browser demo | [matrixmole.com/otp-gadget](https://matrixmole.com/otp-gadget) |
| Build one yourself | [Builder docs](https://matrixmole.com/otp-gadget/docs) |
| Buy a ready-made device or the printable case | [Product page](https://matrixmole.com/products/otp-gadget) |
| Read the firmware source | [`firmware/`](firmware/) |

## How it works (short version)

Two devices generate random bytes during setup, XOR them on one card to produce a shared one-time pad, then split that pad so each side's send-bytes match the other side's receive-bytes. Messages are encoded as QR codes on a 3.5" screen and scanned by the receiver. All key material lives on a per-device MicroSD card, encrypted at rest with AES-256-CTR under a PIN-derived key. The hardware has no radio of any kind.

For the full protocol, threat model, and hardware design, see the [builder docs](https://matrixmole.com/otp-gadget/docs).

## Hardware

Off-the-shelf modules, no custom PCB: Waveshare RP2350-Touch-LCD-3.5 (3.5" capacitive touch + RP2350 microcontroller + onboard battery management + onboard TF slot), UART QR scanner, a 3.3 V SPI MicroSD breakout, and a 1.8 Ah LiPo battery. See [`docs/parts-overview.md`](docs/parts-overview.md) and [`docs/order-checklist.md`](docs/order-checklist.md).

## License

- **Firmware** (`firmware/`, `scripts/`, `main.py`): GPLv3 — [LICENSE-firmware](LICENSE-firmware).
- **Documentation** (`README.md`, `docs/`, `case-design/*.md`, `assembly/`): CC BY-SA 4.0 — [LICENSE-docs](LICENSE-docs).
- **Printable case** (`case-design/Case*.3mf`): not in this repo, sold separately as the Builder Pack. Not covered by either license above.
- **Matrix Mole / OTP Gadget brand**: not licensed. Forks must not present themselves as the official product.
