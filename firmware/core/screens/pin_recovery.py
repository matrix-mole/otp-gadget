import json
import time

from firmware.core.crypto.ctr import aes_ctr_xor
from firmware.core.crypto.kek import derive_kek
from firmware.core.crypto.master_key import (
    check_verify_token,
    make_recovery_key,
    unwrap_dek,
    verify_device_secret,
    wrap_dek,
)
from firmware.core import breadcrumb
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.keyboard import Keyboard
from firmware.core.widgets.keypad import Keypad, DIGIT_LAYOUT, HEX_LAYOUT
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG     = 0x0000
_FG     = 0xFFFF
_TEAL   = 0x0640
_RED    = 0xF800
_YELLOW = 0xFFE0
_GREY   = 0x8C71
_DIM    = 0x2945
_CYAN   = 0x07FF
_GREEN  = 0x07E0

_DIGITS         = frozenset("0123456789")
_HEX_CHARS      = frozenset("0123456789ABCDEF")
_SECRET_HEX_LEN = 64   # 32-byte device_secret = 64 hex chars
_ATTEMPTS_FILE  = "pin_attempts.json"
_WIPE_WORD      = "RESET"


class FactoryResetDone(BaseException):
    """Raised after full factory reset to unwind all the way to main_loop."""


def run_recovery(hal) -> bool:
    """Entry point from PINEntry '?' button.
    Returns True if device is now unlocked (hal.unlock_secrets called), False to re-enter PIN."""
    while True:
        choice = _show_help_modal(hal)
        if choice == "RESTORE":
            if _run_restore(hal):
                return True
        elif choice == "WIPE":
            if _run_wipe(hal):
                return True
        else:
            return False  # Cancel from PINHelp → back to PIN entry


# ── Help modal ────────────────────────────────────────────────────────────────

def _show_help_modal(hal) -> str | None:
    hal.notify_screen("PINHelp")
    breadcrumb.mark(hal, "PINHelp")
    btn_back    = Button(4,   4,   90,  44, "< Back",                    font_14, _FG, 0x4208)
    btn_restore = Button(20, 180, 280,  50, "Restore using backup code",  font_14, _BG, _TEAL)
    btn_wipe    = Button(20, 246, 280,  50, "Wipe card & start fresh",   font_14, _FG, _DIM)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 50,  "PIN Recovery",                          font_28, _FG)
    draw_text_centered(hal, 96,  "Forgot your PIN?",                      font_14, _YELLOW)
    draw_text_centered(hal, 114, "Restore: recover without losing data.", font_14, _GREY)
    draw_text_centered(hal, 130, "Wipe: delete everything & restart.",    font_14, _GREY)
    btn_back.draw(hal)
    btn_restore.draw(hal)
    btn_wipe.draw(hal)

    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_restore.hit_test(x, y):
            return "RESTORE"
        if btn_wipe.hit_test(x, y):
            return "WIPE"
        if btn_back.hit_test(x, y):
            return None


# ── Restore flow ──────────────────────────────────────────────────────────────

def _run_restore(hal) -> bool:
    """Full restore flow. Returns True if device is now unlocked."""
    while True:
        method = _pick_restore_method(hal)
        if method is None:
            return False  # back to PIN entry

        entered = _scan_qr_secret(hal) if method == "QR" else _enter_hex_secret(hal)
        if entered is None:
            continue  # back to method picker

        stored_secret = hal.flash_read("device_secret.bin")
        if not verify_device_secret(entered, stored_secret):
            _show_alert(hal, "Backup code doesn't match.")
            continue

        try:
            raw = hal.read_file("own", "/secret/recovery_token.enc")
        except OSError:
            _show_alert(hal, "Can't recover this card. Try wiping.")
            return False

        dek = unwrap_dek(raw, make_recovery_key(stored_secret))

        # Confirm DEK is correct by decrypting verify.bin directly
        card_salt = hal.read_file("own", "/device/card_salt.bin")
        verify_raw = hal.read_file("own", "/secret/verify.bin")
        iv, ct = verify_raw[:16], verify_raw[16:]
        if not check_verify_token(aes_ctr_xor(dek, iv, ct), card_salt):
            _show_alert(hal, "Recovery data is damaged. Try wiping.")
            return False

        if _set_new_pin(hal, dek):
            return True
        # user backed out of set-new-pin → loop to method picker


def _pick_restore_method(hal) -> str | None:
    hal.notify_screen("PickRestoreMethod")
    breadcrumb.mark(hal, "PickRestoreMethod")
    btn_qr   = Button(20, 160, 280, 50, "Scan QR code",       font_14, _BG, _TEAL)
    btn_hex  = Button(20, 228, 280, 50, "Type it in manually",  font_14, _FG, _DIM)
    btn_back = Button(4,    4,  90, 44, "< Back",              font_14, _FG, 0x4208)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 50,  "Restore Access",                   font_28, _FG)
    draw_text_centered(hal, 104, "Provide your backup code",   font_14, _GREY)
    draw_text_centered(hal, 120, "to restore access:",          font_14, _GREY)
    btn_qr.draw(hal)
    btn_hex.draw(hal)
    btn_back.draw(hal)

    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_qr.hit_test(x, y):
            return "QR"
        if btn_hex.hit_test(x, y):
            return "HEX"
        if btn_back.hit_test(x, y):
            return None


def _scan_qr_secret(hal) -> str | None:
    hal.notify_screen("ScanQRSecret")
    breadcrumb.mark(hal, "ScanQRSecret")
    btn_back = Button(4, 4, 90, 44, "< Back", font_14, _FG, 0x4208)
    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text(hal, 4, 50, "Scan QR Code", font_28, _FG)
    draw_text(hal, 4, 96, "Point scanner at your backup QR code.", font_14, _GREY)
    draw_text(hal, 4, 112, "Scanning...", font_14, _YELLOW)
    btn_back.draw(hal)

    import time as _t
    deadline = _t.monotonic() + 30
    while _t.monotonic() < deadline:
        touch = hal.get_touch()
        if touch is not None:
            x, y = touch
            if btn_back.hit_test(x, y):
                return None
        result = hal.qr_poll()
        if result is not None:
            return result.upper().strip()
        _t.sleep(0.05)

    hal.fill_rect(0, 104, 320, 40, _BG)
    draw_text(hal, 4, 112, "Scan timed out. Tap Back.", font_14, _RED)
    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_back.hit_test(x, y):
            return None


def _enter_hex_secret(hal) -> str | None:
    hal.notify_screen("EnterHexSecret")
    breadcrumb.mark(hal, "EnterHexSecret")
    buf = []
    done = False
    back_pressed = False
    error = ""

    def draw_chrome():
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 50, "Enter your backup code", font_14, _YELLOW)
        draw_text(hal, 4, 68, f"Type all {_SECRET_HEX_LEN} characters:", font_14, _GREY)
        btn_back.draw(hal)
        kb.draw()

    def draw_input():
        hal.fill_rect(0, 90, 320, 100, _BG)
        entered = "".join(buf)
        half = 40
        if len(entered) <= half:
            line1 = " " * half
            line2 = entered + "_" * (half - len(entered))
        else:
            line1 = entered[:half]
            rem = entered[half:]
            line2 = rem + "_" * (half - len(rem))
        draw_text(hal, 0, 97,  line1, font_14, _CYAN)
        draw_text(hal, 0, 113, line2, font_14, _CYAN)
        draw_text(hal, 4, 135, f"{len(entered)} / {_SECRET_HEX_LEN}", font_14, _GREY)
        if error:
            draw_text(hal, 4, 152, error, font_14, _RED)

    def on_char(ch):
        nonlocal error
        if len(buf) < _SECRET_HEX_LEN and ch in _HEX_CHARS:
            buf.append(ch)
            error = ""
            draw_input()

    def on_backspace():
        if buf:
            buf.pop()
            draw_input()

    def on_done():
        nonlocal done, error
        if len(buf) < _SECRET_HEX_LEN:
            error = f"Need {_SECRET_HEX_LEN - len(buf)} more chars"
            draw_input()
        else:
            done = True

    btn_back = Button(4, 4, 90, 44, "< Back", font_14, _FG, 0x4208)
    kb = Keypad(hal, font_28, font_14, on_char, on_backspace, on_done, HEX_LAYOUT)
    draw_chrome()
    draw_input()

    while not done and not back_pressed:
        t = hal.get_touch()
        if t is not None:
            if btn_back.hit_test(*t):
                back_pressed = True
                continue
        kb.update(t)
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)

    return None if back_pressed else "".join(buf)


def _set_new_pin(hal, dek: bytes) -> bool:
    """Enter new PIN twice, re-wrap DEK, write files, unlock. Returns True on success."""
    error = ""
    while True:
        pin1 = _enter_pin(hal, "Set new PIN (4-6 digits)", error)
        if pin1 is None:
            return False
        pin2 = _enter_pin(hal, "Confirm new PIN")
        if pin2 is None:
            error = ""
            continue
        if pin1 == pin2:
            break
        error = "PINs didn't match - try again"

    device_secret = hal.flash_read("device_secret.bin")
    card_salt = hal.read_file("own", "/device/card_salt.bin")
    kdf_params = json.loads(hal.read_file("own", "/device/kdf_params.json"))
    kek = derive_kek(pin1, device_secret, card_salt, kdf_params["iterations"])

    hal.write_file("own", "/secret/master_key.enc",
                   wrap_dek(dek, kek, hal.get_random_bytes(16)))
    hal.write_file("own", "/secret/recovery_token.enc",
                   wrap_dek(dek, make_recovery_key(device_secret), hal.get_random_bytes(16)))
    hal.flash_write(_ATTEMPTS_FILE,
                    json.dumps({"attempts": 0, "cooldown_until": 0}).encode())
    hal.unlock_secrets(dek)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 220, "PIN changed!", font_28, _GREEN)
    hal.feed_watchdog()
    time.sleep(1)
    return True


# ── Wipe flow ─────────────────────────────────────────────────────────────────

def _run_wipe(hal) -> bool:
    while True:
        if not _wipe_confirm(hal):
            return False
        choice = _wipe_choice(hal)
        if choice is None:
            return False
        if choice == "CARD_ONLY":
            _do_wipe(hal)
            from firmware.core.screens.card_init import CardInitScreen
            CardInitScreen(hal).run()
            return hal.file_exists("own", "/secret/verify.bin")
        # FACTORY
        if _factory_reset_confirm(hal):
            _do_wipe(hal)
            hal.flash_delete("device_secret.bin")
            hal.flash_delete(_ATTEMPTS_FILE)
            raise FactoryResetDone()
        # cancelled from FactoryResetConfirm → loop back to WipeConfirm


def _wipe_choice(hal):
    """Show two wipe options after RESET is typed. Returns 'CARD_ONLY', 'FACTORY', or None (back)."""
    hal.notify_screen("WipeChoice")
    breadcrumb.mark(hal, "WipeChoice")
    btn_back    = Button(4,   4,   90,  44, "< Back",             font_14, _FG, 0x4208)
    btn_card    = Button(20, 196, 280,  50, "Wipe card only",     font_14, _FG, _DIM)
    btn_factory = Button(20, 266, 280,  50, "Full factory reset", font_14, _FG, _RED)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 50,  "Choose wipe type",               font_28, _RED)
    draw_text_centered(hal, 104, "Wipe card only: delete all keys", font_14, _GREY)
    draw_text_centered(hal, 120, "and data. Backup code kept.",     font_14, _GREY)
    draw_text_centered(hal, 144, "Full factory reset: also erases", font_14, _GREY)
    draw_text_centered(hal, 160, "backup code. Use to hand off.",   font_14, _GREY)
    btn_back.draw(hal)
    btn_card.draw(hal)
    btn_factory.draw(hal)

    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_back.hit_test(x, y):
            return None
        if btn_card.hit_test(x, y):
            return "CARD_ONLY"
        if btn_factory.hit_test(x, y):
            return "FACTORY"


def _factory_reset_confirm(hal) -> bool:
    """Second confirmation screen for full factory reset. Returns True if confirmed."""
    hal.notify_screen("FactoryResetConfirm")
    breadcrumb.mark(hal, "FactoryResetConfirm")
    btn_cancel  = Button(4,   4,   90,  44, "< Back",        font_14, _FG, 0x4208)
    btn_confirm = Button(40, 370, 240,  50, "CONFIRM RESET", font_14, _FG, _RED)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 50,  "Full Factory Reset",               font_28, _RED)
    draw_text_centered(hal, 110, "This erases ALL data and the",     font_14, _GREY)
    draw_text_centered(hal, 126, "backup code. You cannot",          font_14, _GREY)
    draw_text_centered(hal, 142, "recover anything after this.",     font_14, _GREY)
    draw_text_centered(hal, 170, "Use only to hand off the device",  font_14, _YELLOW)
    draw_text_centered(hal, 186, "or when completely locked out",    font_14, _YELLOW)
    draw_text_centered(hal, 202, "with no backup.",                  font_14, _YELLOW)
    btn_cancel.draw(hal)
    btn_confirm.draw(hal)

    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_cancel.hit_test(x, y):
            return False
        if btn_confirm.hit_test(x, y):
            return True


def _wipe_confirm(hal) -> bool:
    hal.notify_screen("WipeConfirm")
    breadcrumb.mark(hal, "WipeConfirm")
    buf = []
    confirmed = False
    wipe_ready = False

    def draw_chrome():
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 50,  "Wipe all data?",                font_28, _RED)
        draw_text_centered(hal, 90,  "Deletes ALL keys, contacts,",   font_14, _GREY)
        draw_text_centered(hal, 106, "and messages. Irreversible.",    font_14, _GREY)
        draw_text_centered(hal, 130, f'Type "{_WIPE_WORD}" to confirm:', font_14, _YELLOW)
        btn_cancel.draw(hal)
        kb.draw()

    def draw_input():
        hal.fill_rect(0, 148, 320, 100, _BG)
        entered = "".join(buf)
        draw_text_centered(hal, 158, entered + "_" * (len(_WIPE_WORD) - len(entered)),
                           font_28, _CYAN)
        _draw_wipe_button(hal, wipe_ready)

    def on_char(ch):
        nonlocal wipe_ready
        if len(buf) < len(_WIPE_WORD):
            buf.append(ch)
            wipe_ready = "".join(buf).upper() == _WIPE_WORD
            draw_input()

    def on_backspace():
        nonlocal wipe_ready
        if buf:
            buf.pop()
            wipe_ready = "".join(buf).upper() == _WIPE_WORD
            draw_input()

    def on_done():
        nonlocal confirmed
        if "".join(buf).upper() == _WIPE_WORD:
            confirmed = True

    btn_cancel = Button(4, 4, 90, 44, "< Back", font_14, _FG, 0x4208)
    kb = Keyboard(hal, font_28, font_14, on_char, on_backspace, on_done)
    draw_chrome()
    draw_input()

    while not confirmed:
        t = hal.get_touch()
        if t is not None:
            x, y = t
            if btn_cancel.hit_test(x, y):
                return False
            # Wipe button hit area: x=82, y=200, w=156, h=40
            if wipe_ready and 82 <= x < 238 and 200 <= y < 240:
                on_done()
                continue
        kb.update(t)
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)

    return True


def _draw_wipe_button(hal, active: bool) -> None:
    bg = _RED if active else _DIM
    fg = _FG  if active else _GREY
    bx = (320 - 156) // 2  # centered on 320px screen
    hal.fill_rect(bx, 200, 156, 40, bg)
    label = "Wipe"
    gw = font_14.max_width()
    gh = font_14.height()
    tx = bx + (156 - len(label) * gw) // 2
    ty = 200 + (40 - gh) // 2
    draw_text(hal, tx, ty, label, font_14, fg, bg)


def _do_wipe(hal) -> None:
    for path in ("/secret", "/exchange"):
        hal.delete_tree("own", path)


# ── PIN digit entry helper ────────────────────────────────────────────────────

def _enter_pin(hal, title: str, error: str = "") -> str | None:
    """Digit keypad PIN entry. Returns PIN string or None (back)."""
    buf = []
    done = False
    back_pressed = False
    err = error

    def draw_chrome():
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 50, title, font_14, _YELLOW)
        draw_text(hal, 4, 68, "4 to 6 digits", font_14, _GREY)
        btn_back.draw(hal)
        kb.draw()

    def draw_input():
        hal.fill_rect(0, 86, 320, 130, _BG)
        mask = "*" * len(buf) + "-" * (6 - len(buf))
        draw_text(hal, 60, 100, mask, font_28, _CYAN)
        if err:
            draw_text(hal, 4, 185, err, font_14, _RED)
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

    btn_back = Button(4,   4,   90, 44, "< Back", font_14, _FG, 0x4208)
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


# ── Utility ───────────────────────────────────────────────────────────────────

def _show_alert(hal, msg: str) -> None:
    btn_ok = Button(80, 300, 160, 44, "OK", font_14, _BG, _TEAL)
    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text_centered(hal, 220, msg, font_14, _RED)
    btn_ok.draw(hal)
    while True:
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        x, y = t
        if btn_ok.hit_test(x, y):
            return
