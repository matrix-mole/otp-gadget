# Item 5: Guest SD on SPI0 (GPIO3/6/32/33)
#
# PREREQ: MicroSD card breakout wired as follows:
#   MOSI  → GPIO3  (header pin 12)
#   SCK   → GPIO6  (header pin 18)
#   MISO  → GPIO32 (header pin 25)
#   CS    → GPIO33 (header pin 27)
#   3.3V  → pin 31 or 32
#   GND   → pin 29 or 30
#
# PREREQ: sdcard.py must be on the board (copy from
#   docs/waveshare-examples-repo/examples/MicroPython/02_SD/sdcard.py)
#
# SPI0 CONFLICT NOTE: The display (ST7789) also uses SPI0 (GPIO18/19).
# This test runs WITHOUT the display initialized to isolate the SD check.
# In real.py we will need to either time-share SPI0 (reinit before each use)
# or switch the guest SD to SoftSPI on GPIO41-47 to avoid the conflict.

import os
from machine import SPI, Pin

MOSI = 3
SCK  = 6
MISO = 32
CS   = 33

print("=== Item 5: Guest SD on SPI0 (GPIO3/6/32/33) ===")

try:
    spi = SPI(0, baudrate=1_000_000, polarity=0, phase=0, bits=8,
              sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO))
    cs  = Pin(CS, Pin.OUT, value=1)
    print("  SPI0 init: OK")

    import sdcard
    sd = sdcard.SDCard(spi, cs)
    print("  SDCard init: OK")

    os.mount(sd, '/guest')
    listing = os.listdir('/guest')
    print("  Mounted at /guest, contents:", listing)
    os.umount('/guest')
    print("  Unmounted: OK")
    print("PASS")

except ImportError as e:
    print("FAIL: missing module -", e)
    print("  Copy sdcard.py to the board root first.")

except OSError as e:
    print("FAIL: OS error -", e)
    if "no SD card" in str(e):
        print("  Check wiring: no card detected on SPI0.")
    else:
        print("  Action: re-check pin mapping vs RP2350B SPI mux table.")
        print("  Fallback: use SoftSPI on GPIO41-47 if hardware SPI0 conflicts.")

except Exception as e:
    print("FAIL:", type(e).__name__, e)
