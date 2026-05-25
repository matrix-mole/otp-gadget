# Item 8: Display (ST7789) + touch (FT6336U) - no I2C contention
#
# PREREQ: No external wiring needed - display and touch are onboard.
# PREREQ: ST7789.py and FT6336U.py must be on the board (copy from
#   docs/waveshare-examples-repo/examples/MicroPython/01_GUI/)
#
# What this checks:
#   1. ST7789 initializes and fills screen without error
#   2. FT6336U initializes without I2C error (chip ID must be 0x64)
#   3. Touch events are readable (tap the screen when prompted)

import time
from machine import Pin, SPI, I2C

print("=== Item 8: Display + Touch init ===")

# --- Display ---
print("\n[1] ST7789 display init")
try:
    from ST7789 import lcd_st7789
    lcd = lcd_st7789()
    # Fill blue, then red, then black to confirm colors
    RED   = 0xF800
    BLUE  = 0x001F
    BLACK = 0x0000
    lcd.lcd_fill(BLUE)
    time.sleep_ms(300)
    lcd.lcd_fill(RED)
    time.sleep_ms(300)
    lcd.lcd_fill(BLACK)
    print("  ST7789 init and fill: PASS (screen should have flashed blue→red→black)")
except ImportError:
    print("  FAIL: ST7789.py not found on board - copy it first")
except Exception as e:
    print("  FAIL:", type(e).__name__, e)

# --- Touch ---
print("\n[2] FT6336U touch init")
try:
    from FT6336U import touch_ft6336u
    touch = touch_ft6336u()
    # init_chip() prints the chip ID itself; check it came back 0x64
    print("  FT6336U init: OK (chip ID printed above - should be 0x64)")
except ImportError:
    print("  FAIL: FT6336U.py not found on board - copy it first")
except Exception as e:
    print("  FAIL:", type(e).__name__, e)
    raise

# --- Touch readout ---
print("\n[3] Touch read (tap the screen 3 times)")
try:
    taps = 0
    deadline = time.ticks_add(time.ticks_ms(), 10_000)  # 10 s window
    while taps < 3 and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        pt = touch.get_touch_xy()
        if pt:
            x, y = pt[0]["x"], pt[0]["y"]
            print(f"  Tap {taps+1}: raw x={x} y={y}")
            taps += 1
            time.sleep_ms(300)  # debounce
        time.sleep_ms(10)

    if taps == 3:
        print("  Touch read: PASS")
    else:
        print(f"  Touch read: only {taps}/3 taps detected in 10 s - check if driver is printing ID errors above")
except Exception as e:
    print("  FAIL:", type(e).__name__, e)

print("\n=== Done ===")
print("PASS if display flashed colors and 3 touch coords were printed.")
