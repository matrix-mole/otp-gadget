import json
import time

from firmware.core.crypto.ctr import aes_ctr_xor
from firmware.core.crypto.kek import derive_kek
from firmware.core.crypto.master_key import (
    check_verify_token,
    make_recovery_key,
    unwrap_dek,
    wrap_dek,
)
from firmware.core import breadcrumb, settings_store
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.keypad import Keypad, DIGIT_LAYOUT
from firmware.core.widgets.text import draw_text, draw_text_centered, text_width

_BG     = 0x0000
_FG     = 0xFFFF
_GREY   = 0x8C71
_DIM    = 0x2945
_RED    = 0xF800
_CYAN   = 0x07FF
_YELLOW = 0xFFE0
_TEAL   = 0x0640
_GREEN  = 0x07E0

_DIGITS = frozenset("0123456789")

_BURN_TOGGLE_X = 266
_BURN_TOGGLE_Y = 310
_BURN_TOGGLE_W = 50
_BURN_TOGGLE_H = 36


class SettingsScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self) -> str:
        """Returns 'HOME' or 'LOCK'."""
        self._hal.notify_screen("Settings")
        breadcrumb.mark(self._hal, "Settings")
        hal = self._hal
        session = self._session

        controls = self._make_controls()
        self._draw(controls)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            session.record_touch(hal)
            x, y = t
            if controls["pin"].hit_test(x, y):
                result = self._change_pin()
                if result == "LOCK":
                    return "LOCK"
                self._hal.notify_screen("Settings")
                breadcrumb.mark(self._hal, "Settings")
                controls = self._make_controls()
                self._draw(controls)
            elif controls["secret"].hit_test(x, y):
                self._view_device_secret()
                self._hal.notify_screen("Settings")
                breadcrumb.mark(self._hal, "Settings")
                controls = self._make_controls()
                self._draw(controls)
            elif controls["burn_help"].hit_test(x, y):
                result = self._show_burn_after_reading_help()
                if result == "LOCK":
                    return "LOCK"
                settings = settings_store.read_settings(hal)
                settings["burn_after_reading_help_seen"] = True
                settings_store.write_settings(hal, settings)
                self._hal.notify_screen("Settings")
                breadcrumb.mark(self._hal, "Settings")
                controls = self._make_controls()
                self._draw(controls)
            elif controls["burn_toggle"].hit_test(x, y):
                settings = settings_store.read_settings(hal)
                new_value = not settings["burn_after_reading"]
                shown_modal = False
                if new_value and not settings["burn_after_reading_help_seen"]:
                    result = self._show_burn_after_reading_help()
                    if result == "LOCK":
                        return "LOCK"
                    settings["burn_after_reading_help_seen"] = True
                    shown_modal = True
                settings["burn_after_reading"] = new_value
                settings_store.write_settings(hal, settings)
                controls = self._make_controls()
                if shown_modal:
                    self._hal.notify_screen("Settings")
                    breadcrumb.mark(self._hal, "Settings")
                    self._draw(controls)
                else:
                    self._draw_burn_toggle(controls)
            elif controls["back"].hit_test(x, y):
                return "HOME"

    def _make_controls(self) -> dict:
        settings = settings_store.read_settings(self._hal)
        burn_on = settings["burn_after_reading"]
        return {
            "pin": Button(40, 170, 240, 46, "Change PIN",       font_14, _FG, _DIM),
            "secret": Button(40, 230, 240, 46, "View backup code", font_14, _FG, _DIM),
            "burn_help": Button(224, 310, 36, 36, "?", font_14, _FG, _DIM),
            "burn_toggle": Button(_BURN_TOGGLE_X, _BURN_TOGGLE_Y, _BURN_TOGGLE_W, _BURN_TOGGLE_H,
                                  "ON" if burn_on else "OFF",
                                  font_14, _BG if burn_on else _FG, _TEAL if burn_on else _DIM),
            "back": Button(4, 4, 60, 44, "<", font_14, _FG, _DIM),
            "burn_on": burn_on,
        }

    def _draw(self, controls: dict) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 96, 20, "Settings", font_28, _FG)
        controls["pin"].draw(hal)
        controls["secret"].draw(hal)
        draw_text(hal, 8, 318, "Burn after", font_14, _FG)
        draw_text(hal, 8, 336, "reading", font_14, _FG)
        controls["burn_help"].draw(hal)
        controls["burn_toggle"].draw(hal)
        controls["back"].draw(hal)

    def _draw_burn_toggle(self, controls: dict) -> None:
        self._hal.fill_rect(_BURN_TOGGLE_X, _BURN_TOGGLE_Y,
                            _BURN_TOGGLE_W, _BURN_TOGGLE_H, _BG)
        controls["burn_toggle"].draw(self._hal)

    def _show_burn_after_reading_help(self) -> str | None:
        self._hal.notify_screen("BurnAfterReadingHelp")
        breadcrumb.mark(self._hal, "BurnAfterReadingHelp")
        hal = self._hal
        session = self._session
        _MW, _MH = 304, 456
        _MX = (320 - _MW) // 2
        _MY = (480 - _MH) // 2
        _BD = 2
        btn_done = Button(_MX + (_MW - 120) // 2, _MY + _MH - 46 - 16,
                          120, 46, "Done", font_14, _BG, _TEAL)
        lines = [
            "Burn after reading",
            "",
            "Received messages are",
            "shown once.",
            "",
            "They are not saved in",
            "the thread, and used",
            "receive-pad bytes are",
            "scrubbed.",
            "",
            "Not forensic-secure",
            "deletion on MicroSD.",
        ]

        hal.fill_rect(_MX, _MY, _MW, _MH, _BG)
        hal.fill_rect(_MX,             _MY,             _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY + _MH - _BD, _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY,             _BD, _MH, _TEAL)
        hal.fill_rect(_MX + _MW - _BD, _MY,             _BD, _MH, _TEAL)

        line_h = 20
        text_h = len(lines) * line_h
        text_y0 = _MY + (_MH - text_h - 46 - 22) // 2
        for i, line in enumerate(lines):
            if line:
                color = _YELLOW if i == 0 else (_GREY if i >= len(lines) - 2 else _FG)
                lw = text_width(line, font_14)
                draw_text(hal, _MX + (_MW - lw) // 2, text_y0 + i * line_h,
                          line, font_14, color)
        btn_done.draw(hal)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                if btn_done.hit_test(*t):
                    return None
            hal.feed_watchdog()
            time.sleep(0.05)

    # ── View device secret ────────────────────────────────────────────────────

    def _view_device_secret(self) -> None:
        self._hal.notify_screen("ViewDeviceSecret")
        breadcrumb.mark(self._hal, "ViewDeviceSecret")
        hal = self._hal
        from firmware.core.vendor.uQR import ERROR_CORRECT_L
        from firmware.core.widgets.qr import make_qr, draw_qr

        secret = hal.flash_read("device_secret.bin")
        hex_str = secret.hex().upper()
        matrix = make_qr(hex_str, error_correction=ERROR_CORRECT_L)

        _MODULE_PX = 4
        n = len(matrix)
        qr_px = n * _MODULE_PX
        qr_x = (320 - qr_px) // 2
        qr_y = 44
        hex_y = qr_y + qr_px + 8
        btn_y = hex_y + 40

        btn_done = Button(80, btn_y, 160, 40, "Done", font_14, _BG, _TEAL)

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 8,  "Backup code",               font_14, _YELLOW)
        draw_text_centered(hal, 26, "Save this somewhere safe",  font_14, _GREY)
        draw_qr(hal, matrix, qr_x, qr_y, module_px=_MODULE_PX)
        draw_text_centered(hal, hex_y,      hex_str[:32], font_14, _FG)
        draw_text_centered(hal, hex_y + 18, hex_str[32:], font_14, _FG)
        btn_done.draw(hal)

        while True:
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            x, y = t
            if btn_done.hit_test(x, y):
                return

    # ── Change PIN ────────────────────────────────────────────────────────────

    def _change_pin(self) -> str | None:
        """Full ChangePIN flow. Returns 'LOCK' if auto-lock triggered, else None."""
        hal = self._hal
        breadcrumb.mark(hal, "ChangePIN")

        # Step 1: verify current PIN (without disturbing the active DEK in HAL)
        error = ""
        while True:
            pin = self._enter_digit_pin("Enter current PIN", error)
            if pin is None:
                return None  # cancelled
            dek = _verify_pin_get_dek(hal, pin)
            if dek is not None:
                break
            error = "Wrong PIN"

        # Step 2: enter new PIN twice
        error = ""
        while True:
            pin1 = self._enter_digit_pin("Set new PIN (4-6 digits)", error)
            if pin1 is None:
                return None
            pin2 = self._enter_digit_pin("Confirm new PIN")
            if pin2 is None:
                error = ""
                continue
            if pin1 == pin2:
                break
            error = "PINs didn't match - try again"

        # Step 3: re-wrap DEK under new KEK and update recovery token
        device_secret = hal.flash_read("device_secret.bin")
        card_salt = hal.read_file("own", "/device/card_salt.bin")
        kdf_params = json.loads(hal.read_file("own", "/device/kdf_params.json"))
        kek = derive_kek(pin1, device_secret, card_salt, kdf_params["iterations"])

        hal.write_file("own", "/secret/master_key.enc",
                       wrap_dek(dek, kek, hal.get_random_bytes(16)))
        hal.write_file("own", "/secret/recovery_token.enc",
                       wrap_dek(dek, make_recovery_key(device_secret), hal.get_random_bytes(16)))

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 220, "PIN changed!", font_28, _GREEN)
        hal.feed_watchdog()
        time.sleep(1)
        return None

    def _enter_digit_pin(self, title: str, error: str = "") -> str | None:
        """Digit keypad PIN entry. Returns PIN or None (cancelled)."""
        hal = self._hal
        buf = []
        done = False
        back_pressed = False
        err = error

        def draw_chrome():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 4, 8, title, font_14, _YELLOW)
            draw_text(hal, 4, 28, "4 to 6 digits", font_14, _GREY)
            btn_back.draw(hal)
            kb.draw()

        def draw_input():
            hal.fill_rect(0, 55, 320, 128, _BG)
            mask = "*" * len(buf) + "-" * (6 - len(buf))
            draw_text(hal, 60, 68, mask, font_28, _CYAN)
            if err:
                draw_text(hal, 4, 120, err, font_14, _RED)
            btn_back.draw(hal)
            if len(buf) >= 4:
                btn_go.draw(hal)

        def on_char(ch):
            nonlocal err
            if len(buf) < 6 and ch in _DIGITS:
                buf.append(ch)
                err = ""
                draw_input()

        def on_backspace():
            if buf:
                buf.pop()
                draw_input()

        def on_done():
            nonlocal done, err
            if len(buf) < 4:
                err = "Need at least 4 digits"
                draw_input()
            else:
                done = True

        btn_back = Button(4,   148, 90, 44, "< Back", font_14, _FG, 0x4208)
        btn_go   = Button(244, 148, 72, 32, "Go >",   font_14, _BG, _TEAL)
        kb = Keypad(hal, font_28, font_14, on_char, on_backspace, on_done, DIGIT_LAYOUT)
        draw_chrome()
        draw_input()

        while not done and not back_pressed:
            t = hal.get_touch()
            if t is not None:
                x, y = t
                if btn_back.hit_test(x, y):
                    back_pressed = True
                    continue
                if len(buf) >= 4 and btn_go.hit_test(x, y):
                    on_done()
                    continue
            kb.update(t)
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)

        return None if back_pressed else "".join(buf)


# ── Standalone helper (used by both ChangePIN and, via import, future callers) ─

def _verify_pin_get_dek(hal, pin: str) -> bytes | None:
    """Derive DEK from PIN without touching hal.unlock_secrets.
    Returns DEK bytes if PIN correct, None otherwise."""
    device_secret = hal.flash_read("device_secret.bin")
    card_salt = hal.read_file("own", "/device/card_salt.bin")
    kdf_params = json.loads(hal.read_file("own", "/device/kdf_params.json"))
    kek = derive_kek(pin, device_secret, card_salt, kdf_params["iterations"])
    candidate_dek = unwrap_dek(hal.read_file("own", "/secret/master_key.enc"), kek)
    verify_raw = hal.read_file("own", "/secret/verify.bin")
    iv, ct = verify_raw[:16], verify_raw[16:]
    if check_verify_token(aes_ctr_xor(candidate_dek, iv, ct), card_salt):
        return candidate_dek
    return None
