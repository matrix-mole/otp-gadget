# Order checklist

Everything you need to buy to build one OTP gadget. You will want to build at least two (the device only does anything useful when paired with a second one), so order for two builds.

> **Skip the temptation to substitute parts before checking the "Do NOT use" notes below.** Two of the SD card modules sold on AliExpress look almost identical to the one we use but are incompatible. Picking the wrong one costs half a day of debugging.

## Waveshare - https://www.waveshare.com

- [ ] **RP2350-Touch-LCD-3.5** - $25 each - https://www.waveshare.com/rp2350-touch-lcd-3.5.htm
  - 3.5 inch IPS touchscreen, RP2350B MCU, AXP2101 PMIC, onboard MicroSD slot, USB-C. The brain and the screen of the gadget in a single pre-assembled module.

## RS Components - https://www.rs-online.com

- [ ] **RS PRO 3.7V 1800mAh LiPo battery** - ~$21 each - https://no.rs-online.com/web/p/speciality-size-rechargeable-batteries/1449405
  - 53.5 x 35 x 10.4 mm, bare wires. Other 3.7V LiPo cells in similar dimensions work too; this is just the one that fits the case nicely. The deep link above is to the Norway store — RS has equivalent stock in most countries; search the local RS site for the SKU or for "RS PRO 1800 mAh".

## AliExpress - https://www.aliexpress.com

- [ ] **GM861XS QR scanner** - $12 each - https://www.aliexpress.com/item/1005006716675651.html
  - UART (TTL 3.3V), tiny form factor (about 17 mm round, 5 mm deep, 1 g). This is the chosen primary scanner. Other GM-series scanners use the same UART protocol but are physically larger.

- [ ] **Generic MicroSD SPI 3.3V module** (the bare 6-pin variant, no regulator, no level shifter) - $0.50 each - https://www.aliexpress.com/item/1005010794549615.html
  - Used as the external "guest" card slot. **The 3.3V-only constraint is non-negotiable** - see the "Do NOT use" section below.

- [ ] **MX1.25 2-pin cable, single head, 10 cm** - $1.30 - https://www.aliexpress.com/item/1005007277110532.html
  - Connects the battery to the JST socket on the Waveshare board. Order one pack; one cable per gadget.

## Card storage - any retailer

- [ ] **2x MicroSD card, 32 GB, Class 10** - one for your own card, one for the guest slot of whoever you pair with.
  - We use the Intenso 32 GB Class 10 (Norwegian retailer batterionline.no: https://www.batterionline.no/intenso-32-gb-micro-sd-hukommelseskort-incl-adapter-class-10), but any reputable Class 10 card from a major brand works (SanDisk, Kingston, Samsung). Avoid no-name cards from random AliExpress listings - card reliability matters for OTP key material.

---

## Do NOT use

These look almost identical to the SD module above and will break the gadget. They are listed here so you can recognize and reject them.

- ❌ **MicroSD SPI module with level converter** (5V/3.3V, has an onboard AMS1117 regulator)
  - https://www.aliexpress.com/item/1005001309671718.html
  - Why: requires 4.5-5.5 V on VCC. The gadget's header only exposes 3.3 V on battery power. The card will fail ACMD41 init with `OSError: timeout waiting for v2 card` after about 50 seconds of retries.
  - How to recognize: 4-pin SOT-223 regulator on the board, optional 8-pin level-shifter IC, listing claims "5V/3.3V" or "4.5-5.5V".

- ❌ **Mini MicroSD SPI standard variant** (also has onboard regulator)
  - https://www.aliexpress.com/item/32346771288.html
  - Same problem as above.

**What to look for** when sourcing a different SD module than the one linked:

- Listing explicitly says "3.3V only" (not "5V/3.3V")
- Small board (~18 x 18 mm), exactly 6 pins (`CS, SCK, MOSI, MISO, VCC, GND`)
- No visible ICs at all on the board - just the card socket and the pin header
- No mention of "level converter", "AMS1117", or any regulator chip

---

## Tools you also need (not included in the pack)

These are normal hobby electronics tools. If you already build things at home you probably have all of them.

- 3D printer (or access to one). FDM, build volume at least 150 x 100 x 60 mm.
- Soldering iron with a fine tip, solder, flux.
- Small Phillips screwdriver.
- Helping hands or a small vise.
- USB-C data cable (not charge-only).
- Multimeter (optional, useful for debugging).
