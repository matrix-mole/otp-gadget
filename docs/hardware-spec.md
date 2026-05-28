# Hardware specification

Component-level detail for the OTP gadget hardware. For the buying guide (SKUs, prices, "do NOT use" notes), see [`order-checklist.md`](order-checklist.md). For chip-level identity at a security-review level, see [`parts-overview.md`](parts-overview.md).

## Hardware overview

- 3.5" capacitive touchscreen (phone-style touch, no stylus needed).
- QR code send/receive: sender's device displays ciphertext as a QR code; receiver's device scans it using a dedicated QR scanner module, eliminating manual hex entry entirely.
- 3D-printed casing with screwed-in components.
- One external USB-C port (charging + dev; see [USB ports](#usb-ports) below).
- Two MicroSD card slots: own card (internal TF slot on the display board) + guest card (external SPI breakout, accessible from outside the case for key exchange).
- On/off button (built into the display board).
- True random number generator via the RP2350's built-in hardware TRNG.

The device has two exposed MicroSD/TF card slots with fixed roles:

- **Onboard TF slot**: this device's permanent main card (`own card`).
- **External SPI MicroSD slot**: temporary guest/exchange card (`guest card`).

## USB ports

The device has one USB-C port, built into the RP2350-Touch-LCD-3.5 board.

**Single USB-C port — charging and dev:**

- Handles both LiPo battery charging (via the onboard AXP2101 power management chip) and firmware flashing during development.
- Mounted so it is accessible from the outside of the case.
- In normal use, the user plugs in any USB-C charger. No separate charging module or USB-C breakout board is needed.
- **Plugging in the cable does not power the device on.** The AXP2101 charges the battery in the background while the MCU stays off. To power on while charging, press the PWR button as normal.

## Battery

Single-cell 3.7 V LiPo (~1800 mAh) connected to the Waveshare board's onboard JST socket via the MX1.25 2-pin cable listed in [`order-checklist.md`](order-checklist.md).

**Polarity warning.** The JST socket on the Waveshare board is keyed but the MX1.25 cables shipped with batteries are not standardised in colour or pin order across suppliers. **Verify polarity with a multimeter before plugging the battery in:** red wire → `+`, black wire → `-`. Reversed polarity will damage the AXP2101 PMIC and brick the board. If the cable's pinout doesn't match the socket, swap the wires at the MX1.25 connector (the contacts pop out with a small pick).

## Physical controls

- `PWR`: short press while on → immediately powers off (DEK zeroed, device shuts down); short press while off → powers on (boots to PIN entry); long press → hardware forced power-off via AXP2101 (fallback if firmware is frozen). **Inserting a USB-C cable does not count as a power-on** — only a deliberate button press boots the device.
- `RESET`: accessible via a small recessed hole for recovery/debugging.
- `BOOT`: not exposed externally; only used for firmware flashing/recovery during development.

## GPIO pin allocation

The RP2350-Touch-LCD-3.5 board exposes **22 GPIO pins** via a 2.54 mm pin header. Most onboard peripherals (display, TF slot, IMU, audio codec, RTC, power management) are wired to internal GPIOs that are not brought out to the header. **The capacitive touch chip (FT6336) is the exception**: its I2C bus is wired to GPIO34 (SDA) and GPIO35 (SCL), and those same GPIOs are also exposed on header pins 28 and 26. Wiring anything to those header pins puts a second device on the touch I2C bus — touch breaks, the added device breaks, or both. Touch also uses GPIO24 (reset) and GPIO25 (interrupt), but those are not on the header.

**Rule of thumb for future wiring:** GPIO34 and GPIO35 are off-limits for external modules. Treat the header pinout printed in the board datasheet as indicative of which pins exist, not as a guarantee that each one is free.

Consult Waveshare's official board datasheet for the full header pinout, and the GPIO function table (touch, onboard TF, audio codec, RTC, IMU, AXP2101) for which pins are actually free.

**Planned allocation:**

| GPIO   | Header pin | Planned use                  |
| ------ | ---------- | ---------------------------- |
| GPIO4  | 14         | UART1 TX → QR scanner RX     |
| GPIO5  | 16         | UART1 RX ← QR scanner TX     |
| GPIO3  | 12         | SoftSPI MOSI — guest SD card |
| GPIO6  | 18         | SoftSPI SCK — guest SD card  |
| GPIO32 | 25         | SoftSPI MISO — guest SD card |
| GPIO33 | 27         | SoftSPI CS — guest SD card   |

All used pins sit on the half of the header opposite the battery (pins 11–28). Pins 5–10 are left unused because the battery housing sits above them in the case. Power: 3V3 from pin 31 or 32, GND from pin 29 or 30.

**SPI bus separation:** The onboard TF slot is internally wired to GPIO26/27/28/31 (SPI1). The guest SD breakout uses `SoftSPI` on GPIO3/6/32/33 — software-bitbanged, so it does not use any hardware SPI peripheral. The LCD uses hardware SPI0 (GPIO18/19). All three are fully independent and cannot interfere with each other.

**Reserved (do not use):** GPIO34 (pin 28) and GPIO35 (pin 26) — shared with the onboard touch chip's I2C bus.

**Free GPIO pins:** GPIO0, GPIO1, GPIO2, GPIO7, GPIO8, GPIO9, GPIO41, GPIO42, GPIO43, GPIO44, GPIO45, GPIO46, GPIO47 (13 pins available for future use — but GPIO0, GPIO1, GPIO2, GPIO7, GPIO8, GPIO9 are under the battery and not physically reachable).

**Own SD card:** Handled by the onboard TF card slot (internally connected to the RP2350B). No external breakout or GPIO pins required for the own card.
