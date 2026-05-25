# Item 13: Touch alignment check (run after the MADCTL un-mirror fix)
#
# PREREQ: firmware/ package uploaded to the board (uses
#   firmware.hal.drivers.st7789 / ft6336u and the same touch mapping as
#   firmware.hal.real.RealHAL.get_touch).
#
# What this does:
#   Draws a small target square at five known portrait coordinates, one at a
#   time, and waits for the user to tap it. For each tap it prints:
#     - the target's portrait (x, y)
#     - the raw chip (x, y) reported by FT6336U
#     - the mapped portrait (x, y) produced by the same formula as get_touch()
#
# How to interpret:
#   - Mapped (x, y) ≈ target (x, y)        → touch is aligned, no fix needed.
#   - Mapped x ≈ 319 - target x            → X is mirrored; flip portrait_x to
#                                            319 - raw_y in real.py.get_touch.
#   - Mapped y ≈ 479 - target y            → Y is mirrored; flip portrait_y.
#   - Both off                             → axes are also swapped; rework the
#                                            mapping from scratch.

import time
from firmware.hal.drivers.st7789 import lcd_st7789
from firmware.hal.drivers.ft6336u import touch_ft6336u

LCD_W = 320
LCD_H = 480
TARGET = 30  # square edge in pixels

WHITE = 0xFFFF
BLACK = 0x0000
RED   = 0xF800

TARGETS = [
    ("top-left",      30,         30),
    ("top-right",     LCD_W - 30, 30),
    ("center",        LCD_W // 2, LCD_H // 2),
    ("bottom-left",   30,         LCD_H - 30),
    ("bottom-right",  LCD_W - 30, LCD_H - 30),
]


def map_portrait(raw_x, raw_y):
    """Same mapping as firmware.hal.real.RealHAL.get_touch."""
    return (raw_y, 479 - raw_x)


def draw_target(lcd, cx, cy):
    lcd.lcd_fill(BLACK)
    half = TARGET // 2
    x0 = max(0, cx - half)
    y0 = max(0, cy - half)
    lcd.fill_rect(x0, y0, TARGET, TARGET, WHITE)
    # crosshair to make the centre obvious
    lcd.fill_rect(cx, y0, 1, TARGET, RED)
    lcd.fill_rect(x0, cy, TARGET, 1, RED)


def wait_tap(touch, timeout_ms=15_000):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        pt = touch.get_touch_xy()
        if pt:
            return pt[0]["x"], pt[0]["y"]
        time.sleep_ms(10)
    return None


def main():
    lcd = lcd_st7789()
    touch = touch_ft6336u()

    print("=== Touch alignment check ===")
    print("Tap the white square (red crosshair = exact target).")
    print()

    results = []
    for name, tx, ty in TARGETS:
        draw_target(lcd, tx, ty)
        print("  Target {:<13} portrait=({:>3}, {:>3})  ->  tap it".format(
            name, tx, ty))
        tap = wait_tap(touch)
        if tap is None:
            print("    (timeout - skipped)")
            results.append((name, tx, ty, None, None, None, None))
            continue
        raw_x, raw_y = tap
        mx, my = map_portrait(raw_x, raw_y)
        print("    raw=({:>3}, {:>3})  mapped=({:>3}, {:>3})  dx={:>+4}  dy={:>+4}".format(
            raw_x, raw_y, mx, my, mx - tx, my - ty))
        results.append((name, tx, ty, raw_x, raw_y, mx, my))
        time.sleep_ms(300)  # debounce before next target

    lcd.lcd_fill(BLACK)

    # Summary
    print()
    print("=== Summary ===")
    x_mirror_votes = 0
    y_mirror_votes = 0
    n = 0
    for name, tx, ty, rx, ry, mx, my in results:
        if mx is None:
            continue
        n += 1
        # Closer to mirrored than to direct?
        if abs(mx - (319 - tx)) < abs(mx - tx):
            x_mirror_votes += 1
        if abs(my - (479 - ty)) < abs(my - ty):
            y_mirror_votes += 1
    if n == 0:
        print("No taps recorded.")
        return
    print("X mirrored on {}/{} taps".format(x_mirror_votes, n))
    print("Y mirrored on {}/{} taps".format(y_mirror_votes, n))
    if x_mirror_votes == 0 and y_mirror_votes == 0:
        print("=> Touch mapping looks correct. No HAL change needed.")
    else:
        if x_mirror_votes >= n // 2 + 1:
            print("=> Touch X is mirrored. In real.py get_touch, change")
            print("   `return (raw_y, 479 - raw_x)` to `(319 - raw_y, 479 - raw_x)`.")
        if y_mirror_votes >= n // 2 + 1:
            print("=> Touch Y is mirrored. In real.py get_touch, change")
            print("   the second component from `479 - raw_x` to `raw_x`.")


main()
