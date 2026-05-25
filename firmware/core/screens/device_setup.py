import time

from firmware.core import breadcrumb
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.keypad import Keypad, HEX_LAYOUT
from firmware.core.widgets.qr import draw_qr as _draw_qr_shared, make_qr as _make_qr_shared
from firmware.core.widgets.text import draw_text

_BG = 0x0000
_FG = 0xFFFF
_YELLOW = 0xFFE0
_GREY = 0x8C71
_TEAL = 0x0640
_RED = 0xF800
_GREEN = 0x07E0
_CYAN = 0x07FF

_HEX_CHARS = frozenset('0123456789ABCDEF')
_MODULE_PX = 4


def _make_qr(data: str):
    from firmware.core.vendor.uQR import ERROR_CORRECT_L
    return _make_qr_shared(data, error_correction=ERROR_CORRECT_L)


def _draw_qr(hal, matrix, x0: int, y0: int) -> None:
    _draw_qr_shared(hal, matrix, x0, y0, module_px=_MODULE_PX)


class DeviceSetupScreen:
    """Shown once on first boot when device_secret.bin is absent."""

    def __init__(self, hal):
        self._hal = hal

    def run(self) -> None:
        self._hal.notify_screen("DeviceSetup")
        breadcrumb.mark(self._hal, "DeviceSetup")
        hal = self._hal
        secret = hal.get_random_bytes(32)
        hal.flash_write("device_secret.bin", secret)
        hex_str = secret.hex().upper()
        matrix = _make_qr(hex_str)

        n = len(matrix)
        qr_px = n * _MODULE_PX
        qr_x = (320 - qr_px) // 2
        qr_y = 44
        hex_y = qr_y + qr_px + 8
        btn_y = hex_y + 36

        btn_save = Button(4, btn_y, 148, 40, "I've saved it", font_14, _FG, _TEAL)
        btn_skip = Button(160, btn_y, 156, 40, "Skip for now", font_14, _FG, 0x4208)

        def draw_main():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 4, 8, "Save your backup code", font_14, _YELLOW)
            draw_text(hal, 4, 26, "Store this somewhere safe", font_14, _GREY)
            _draw_qr(hal, matrix, qr_x, qr_y)
            draw_text(hal, 4, hex_y, hex_str[:32], font_14, _FG)
            draw_text(hal, 4, hex_y + 18, hex_str[32:], font_14, _FG)
            btn_save.draw(hal)
            btn_skip.draw(hal)

        draw_main()

        while True:
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            x, y = t
            if btn_save.hit_test(x, y):
                if self._confirm_flow(hal, hex_str):
                    return
                draw_main()
            elif btn_skip.hit_test(x, y):
                return

    def _confirm_flow(self, hal, hex_str: str) -> bool:
        """Keyboard sub-flow: user types last 8 hex chars. Returns True on match."""
        buf = []
        confirmed = False
        error = ""

        def draw_chrome():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 4, 8, "Confirm backup", font_14, _YELLOW)
            draw_text(hal, 4, 26, "Enter the last 8 characters:", font_14, _GREY)
            btn_back.draw(hal)
            kb.draw()

        def draw_input():
            hal.fill_rect(0, 70, 320, 78, _BG)
            entered = ''.join(buf)
            draw_text(hal, 60, 80, entered + '_' * (8 - len(entered)), font_28, _CYAN)
            if error:
                draw_text(hal, 4, 120, error, font_14, _RED)
            btn_back.draw(hal)

        def on_char(ch):
            nonlocal error
            if len(buf) < 8 and ch in _HEX_CHARS:
                buf.append(ch)
                error = ""
                draw_input()

        def on_back():
            if buf:
                buf.pop()
                draw_input()

        def on_done():
            nonlocal confirmed, error
            entered = ''.join(buf)
            if len(entered) < 8:
                error = "Need 8 characters"
                draw_input()
            elif entered == hex_str[-8:]:
                hal.fill_rect(0, 70, 320, 78, _BG)
                draw_text(hal, 4, 80, "Backup confirmed!", font_14, _GREEN)
                hal.feed_watchdog()
                time.sleep(1)
                confirmed = True
            else:
                error = "No match - try again"
                buf.clear()
                draw_input()

        btn_back = Button(4, 148, 90, 44, "< Back", font_14, _FG, 0x4208)
        kb = Keypad(hal, font_28, font_14, on_char, on_back, on_done, HEX_LAYOUT)
        draw_chrome()
        draw_input()

        while not confirmed:
            t = hal.get_touch()
            if t is not None:
                if btn_back.hit_test(*t):
                    return False
            kb.update(t)
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)

        return True
