import json
import time

from firmware.core import breadcrumb
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.keypad import Keypad, DIGIT_LAYOUT
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG = 0x0000
_FG = 0xFFFF
_YELLOW = 0xFFE0
_GREY = 0x8C71
_TEAL = 0x0640
_RED = 0xF800
_CYAN = 0x07FF
_GREEN = 0x07E0
_DIGITS = frozenset('0123456789')
_SIM_PBKDF2_ITERATIONS = 1000


class CardInitScreen:
    """Shown when own card has no /secret/verify.bin."""

    def __init__(self, hal):
        self._hal = hal

    def run(self) -> None:
        self._hal.notify_screen("CardInit")
        breadcrumb.mark(self._hal, "CardInit")
        self._confirm_dialog()
        pin = self._pin_setup_flow()
        if pin is None:
            return
        self._init_card(pin)
        self._success_screen()

    def _confirm_dialog(self) -> None:
        hal = self._hal
        btn_init = Button(80, 280, 160, 44, "Set up", font_14, _BG, _TEAL)

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 80,  "New card inserted.",    font_14, _YELLOW)
        draw_text_centered(hal, 100, "Set up this card?",     font_14, _FG)
        draw_text_centered(hal, 140, "Sets up encryption keys.", font_14, _GREY)
        draw_text_centered(hal, 158, "You will set a PIN.",      font_14, _GREY)
        btn_init.draw(hal)

        while True:
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            x, y = t
            if btn_init.hit_test(x, y):
                return

    def _pin_setup_flow(self) -> str | None:
        """Enter PIN twice for confirmation. Returns PIN or None if backed out."""
        error = ""
        while True:
            pin1 = self._enter_pin("Set your PIN (4-6 digits)", error=error)
            if pin1 is None:
                return None
            pin2 = self._enter_pin("Confirm your PIN")
            if pin2 is None:
                error = ""
                continue
            if pin1 == pin2:
                return pin1
            error = "PINs didn't match - try again"

    def _enter_pin(self, title: str, error: str = "") -> str | None:
        hal = self._hal
        buf = []
        done = False
        back_pressed = False
        err = error

        def draw_chrome():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text_centered(hal, 8,  title,          font_14, _YELLOW)
            draw_text_centered(hal, 28, "4 to 6 digits", font_14, _GREY)
            btn_back.draw(hal)
            kb.draw()

        def draw_input():
            hal.fill_rect(0, 55, 320, 134, _BG)
            mask = '*' * len(buf) + '-' * (6 - len(buf))
            draw_text_centered(hal, 68, mask, font_28, _CYAN)
            if err:
                draw_text_centered(hal, 120, err, font_14, _RED)
            btn_back.draw(hal)
            ready = len(buf) >= 4
            if ready:
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

        btn_back = Button(4, 148, 90, 44, "< Back", font_14, _FG, 0x4208)
        btn_go = Button(244, 148, 72, 32, "Go >", font_14, _BG, _TEAL)
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

        if back_pressed:
            return None
        return ''.join(buf)

    def _init_card(self, pin: str) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 232, "Setting up card...", font_14, _GREY)

        assert hal.flash_exists("device_secret.bin"), "device_secret.bin missing - run device setup first"
        device_secret = hal.flash_read("device_secret.bin")
        card_salt = hal.get_random_bytes(32)
        dek = hal.get_random_bytes(32)
        kek_iv = hal.get_random_bytes(16)

        from firmware.core.crypto.kek import derive_kek
        from firmware.core.crypto.master_key import make_verify_token, make_recovery_key, wrap_dek

        kek = derive_kek(pin, device_secret, card_salt, _SIM_PBKDF2_ITERATIONS)
        master_key_enc = wrap_dek(dek, kek, kek_iv)
        recovery_key = make_recovery_key(device_secret)
        recovery_iv = hal.get_random_bytes(16)
        recovery_token_enc = wrap_dek(dek, recovery_key, recovery_iv)
        verify_token = make_verify_token(card_salt)

        hal.write_file("own", "/device/card_salt.bin", card_salt)
        hal.write_file("own", "/device/kdf_params.json",
                       json.dumps({"iterations": _SIM_PBKDF2_ITERATIONS}).encode())
        hal.write_file("own", "/device/version.txt", b"v1")
        hal.write_file("own", "/secret/master_key.enc", master_key_enc)
        hal.write_file("own", "/secret/recovery_token.enc", recovery_token_enc)
        hal.unlock_secrets(dek)
        hal.write_secret("/secret/verify.bin", verify_token)

        from firmware.core.contacts_store import init_manifest
        init_manifest(hal)

    def _success_screen(self) -> None:
        hal = self._hal
        btn_done = Button(80, 340, 160, 44, "Done", font_14, _BG, _TEAL)

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 220, "Card is ready!", font_14, _GREEN)
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
