import json
import time

from firmware.core import breadcrumb
from firmware.core.crypto.kek import derive_kek
from firmware.core.crypto.master_key import check_verify_token, unwrap_dek
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.keypad import Keypad, DIGIT_LAYOUT
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG     = 0x0000
_FG     = 0xFFFF
_CYAN   = 0x07FF
_YELLOW = 0xFFE0
_RED    = 0xF800
_GREY   = 0x8C71
_DIM    = 0x2945

_ATTEMPTS_FILE = "pin_attempts.json"
_DIGITS = frozenset("0123456789")

# Power off after 10 minutes of no touch at the PIN screen.
# Combined with the 5-minute auto-lock, total idle time before shutdown is 15 minutes.
_AUTO_POWEROFF_MS = 10 * 60 * 1000


def _ticks_diff(newer: int, older: int) -> int:
    return (newer - older) & 0x3FFFFFFF


def _fmt_seconds(secs: int) -> str:
    if secs >= 60:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s"
    return f"{secs}s"


class PINEntryScreen:

    def __init__(self, hal):
        self._hal = hal
        self._poweroff_ref_ms = hal.ticks_ms()

    def _record_pin_touch(self) -> None:
        self._poweroff_ref_ms = self._hal.ticks_ms()

    def _check_poweroff(self) -> None:
        if _ticks_diff(self._hal.ticks_ms(), self._poweroff_ref_ms) >= _AUTO_POWEROFF_MS:
            self._hal.power_off()

    def run(self) -> None:
        """Block until PIN verified. Calls hal.unlock_secrets(dek) on success."""
        self._hal.notify_screen("PINEntry")
        breadcrumb.mark(self._hal, "PINEntry")
        error = ""
        while True:
            self._check_poweroff()
            state = self._load_state()
            remaining = state["cooldown_until"] - self._hal.rtc_now()
            if remaining > 0:
                help_pressed = self._show_cooldown(state["cooldown_until"])
                error = ""
                if help_pressed:
                    from firmware.core.screens.pin_recovery import run_recovery
                    if run_recovery(self._hal):
                        return
                    self._hal.notify_screen("PINEntry")
                    breadcrumb.mark(self._hal, "PINEntry")
                continue

            result = self._enter_pin(error)
            error = ""

            if result is None:  # ? button pressed
                from firmware.core.screens.pin_recovery import run_recovery
                if run_recovery(self._hal):
                    return  # device unlocked via recovery flow
                self._hal.notify_screen("PINEntry")
                breadcrumb.mark(self._hal, "PINEntry")
                continue

            pin = result

            # Pre-verification: persist incremented counter before checking PIN
            new_attempts = state["attempts"] + 1
            self._save_state({"attempts": new_attempts, "cooldown_until": 0})

            self._show_checking()
            ok, dek = self._verify_pin(pin)

            if ok:
                self._save_state({"attempts": 0, "cooldown_until": 0})
                self._hal.unlock_secrets(dek)
                return

            # Wrong PIN - compute cooldown if threshold reached
            if new_attempts >= 5:
                cooldown_secs = 10 * (2 ** (new_attempts - 5))
                cooldown_until = self._hal.rtc_now() + cooldown_secs
                self._save_state({"attempts": new_attempts, "cooldown_until": cooldown_until})
                error = f"Wrong PIN. Wait {_fmt_seconds(cooldown_secs)}"
            else:
                free = 5 - new_attempts
                error = f"Wrong PIN - {free} {'try' if free == 1 else 'tries'} left"

    def _load_state(self) -> dict:
        hal = self._hal
        if hal.flash_exists(_ATTEMPTS_FILE):
            try:
                state = json.loads(hal.flash_read(_ATTEMPTS_FILE))
                if isinstance(state, dict) and "attempts" in state and "cooldown_until" in state:
                    return state
            except Exception:
                pass
            # File exists but is corrupted - treat as at threshold to preserve rate-limiting
            return {"attempts": 5, "cooldown_until": 0}
        return {"attempts": 0, "cooldown_until": 0}

    def _save_state(self, state: dict) -> None:
        self._hal.flash_write(_ATTEMPTS_FILE, json.dumps(state).encode())

    def _verify_pin(self, pin: str) -> tuple:
        hal = self._hal
        device_secret = hal.flash_read("device_secret.bin")
        card_salt = hal.read_file("own", "/device/card_salt.bin")
        kdf_params = json.loads(hal.read_file("own", "/device/kdf_params.json"))
        kek = derive_kek(pin, device_secret, card_salt, kdf_params["iterations"])
        raw = hal.read_file("own", "/secret/master_key.enc")
        candidate_dek = unwrap_dek(raw, kek)
        hal.unlock_secrets(candidate_dek)
        try:
            plaintext = hal.read_secret("/secret/verify.bin")
        finally:
            hal.lock_secrets()
        if check_verify_token(plaintext, card_salt):
            return True, candidate_dek
        return False, None

    def _show_checking(self) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 220, "Checking...", font_28, _GREY)

    def _show_cooldown(self, cooldown_until: int) -> bool:
        """Returns True if the ? button was tapped, False when cooldown expires."""
        from firmware.core.widgets.button import Button
        hal = self._hal
        btn_help = Button(282, 4, 36, 36, "?", font_14, _FG, _DIM)

        # Draw static chrome once
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 100, "Too many wrong PINs.", font_14, _RED)
        draw_text_centered(hal, 124, "Please wait:",         font_14, _GREY)
        btn_help.draw(hal)

        last_remaining = -1
        while True:
            remaining = cooldown_until - hal.rtc_now()
            if remaining <= 0:
                return False
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            self._check_poweroff()
            if remaining != last_remaining:
                hal.fill_rect(0, 148, 320, 60, _BG)
                draw_text_centered(hal, 170, _fmt_seconds(remaining), font_28, _YELLOW)
                last_remaining = remaining
            t = hal.get_touch()
            if t is not None:
                self._record_pin_touch()
                x, y = t
                if btn_help.hit_test(x, y):
                    return True
            hal.feed_watchdog()
            time.sleep(0.05)

    def _enter_pin(self, error: str = "") -> str | None:
        """Returns PIN string on submit, or None if the ? button was tapped."""
        from firmware.core.widgets.button import Button
        hal = self._hal
        buf = []
        done = False
        help_pressed = False
        local_err = ""

        btn_help = Button(282, 4, 36, 36, "?", font_14, _FG, _DIM)

        def draw_chrome():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 88, 20, "Enter PIN", font_28, _FG)
            btn_help.draw(hal)
            kb.draw()

        def draw_input():
            hal.fill_rect(0, 90, 320, 90, _BG)
            mask = "*" * len(buf) + "-" * (6 - len(buf))
            draw_text(hal, 112, 100, mask, font_28, _CYAN)
            err = local_err or error
            if err:
                draw_text_centered(hal, 160, err, font_14, _RED)

        def on_char(ch):
            nonlocal local_err
            if len(buf) < 6 and ch in _DIGITS:
                buf.append(ch)
                local_err = ""
                draw_input()

        def on_backspace():
            if buf:
                buf.pop()
                draw_input()

        def on_done():
            nonlocal done, local_err
            if len(buf) >= 4:
                done = True
            else:
                local_err = "Need at least 4 digits"
                draw_input()

        kb = Keypad(hal, font_28, font_14, on_char, on_backspace, on_done, DIGIT_LAYOUT)
        draw_chrome()
        draw_input()

        while not done and not help_pressed:
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            self._check_poweroff()
            t = hal.get_touch()
            if t is not None:
                self._record_pin_touch()
                if btn_help.hit_test(*t):
                    help_pressed = True
                    continue
            kb.update(t)
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)

        if help_pressed:
            return None
        return "".join(buf)
