# Item 6: Both SD slots mounted simultaneously (SPI0 guest + SPI1 own)
#
# PREREQ: Item 5 must pass first (guest SD wired on GPIO3/6/32/33).
# PREREQ: Own card inserted in the onboard TF slot.
# PREREQ: sdcard.py must be on the board.
#
# Verifies that SPI0 (guest, GPIO3/6/32/33) and SPI1 (own, GPIO26/27/28/31)
# do not interfere when both are in use.

import os
from machine import SPI, Pin

print("=== Item 6: Dual SD (SPI1 own + SPI0 guest) ===")

try:
    # Mount own card on SPI1 (GPIO26/27/28/31)
    spi1 = SPI(1, baudrate=10_000_000, polarity=0, phase=0, bits=8,
               sck=Pin(26), mosi=Pin(27), miso=Pin(28))
    cs1  = Pin(31, Pin.OUT, value=1)

    import sdcard
    sd1 = sdcard.SDCard(spi1, cs1)
    os.mount(sd1, '/sd')
    print("  Own card (SPI1) mounted at /sd:", os.listdir('/sd')[:5], "...")

    # Mount guest card on SPI0 (GPIO3/6/32/33)
    spi0 = SPI(0, baudrate=1_000_000, polarity=0, phase=0, bits=8,
               sck=Pin(6), mosi=Pin(3), miso=Pin(32))
    cs0  = Pin(33, Pin.OUT, value=1)
    sd0  = sdcard.SDCard(spi0, cs0)
    os.mount(sd0, '/guest')
    print("  Guest card (SPI0) mounted at /guest:", os.listdir('/guest'))

    # Verify own card still readable after guest mount
    own_check = os.listdir('/sd')[:3]
    print("  Own card re-read after guest mount:", own_check)

    os.umount('/guest')
    os.umount('/sd')
    print("  Both unmounted: OK")
    print("PASS")

except Exception as e:
    print("FAIL:", type(e).__name__, e)
    print("  Check for bus interference or CS pin leakage between SPI0/SPI1.")
