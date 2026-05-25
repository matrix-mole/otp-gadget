import time

from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.text import draw_text_centered

_BG   = 0x0000
_FG   = 0xFFFF
_RED  = 0xF800
_GREY = 0x8C71
_TEAL = 0x0640

_LINE_W = 38  # max chars per line at font_14 (8 px/char, 320 px wide)


def _wrap(text: str) -> list:
    lines = []
    while text:
        if len(text) <= _LINE_W:
            lines.append(text)
            break
        cut = text.rfind(" ", 0, _LINE_W)
        if cut <= 0:
            cut = _LINE_W
        lines.append(text[:cut])
        text = text[cut:].lstrip()
    return lines


class RecoveryScreen:

    def __init__(self, hal, error_msg: str, trail: list):
        self._hal = hal
        self._error_msg = error_msg
        self._trail = trail

    def run(self) -> None:
        hal = self._hal
        hal.notify_screen("Recovery")
        btn = Button(40, 420, 240, 40, "Return to PIN entry", font_14, _FG, _GREY)
        self._draw(btn)
        while True:
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is None:
                time.sleep(0.05)
                continue
            if btn.hit_test(*t):
                return

    def _draw(self, btn) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)

        draw_text_centered(hal, 14, "Error", font_28, _RED)

        y = 60
        for line in _wrap(self._error_msg):
            draw_text_centered(hal, y, line, font_14, _FG)
            y += 18

        y = max(y + 12, 120)
        draw_text_centered(hal, y, "Recent screens:", font_14, _GREY)
        y += 18

        trail_entries = self._trail[-6:] if len(self._trail) > 6 else self._trail
        trail_str = " > ".join(trail_entries) if trail_entries else "(none)"
        for line in _wrap(trail_str)[:2]:
            draw_text_centered(hal, y, line, font_14, _TEAL)
            y += 18

        y = max(y + 20, 210)
        draw_text_centered(hal, y,      "Something went wrong, sorry.", font_14, _GREY)
        draw_text_centered(hal, y + 18, "Please photo this screen", font_14, _GREY)
        draw_text_centered(hal, y + 36, "and email it to:", font_14, _GREY)
        draw_text_centered(hal, y + 54, "atas@matrixmole.com", font_14, _FG)

        btn.draw(hal)
