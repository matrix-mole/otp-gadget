import time

from firmware.core import breadcrumb, bookkeeping, contacts_store, settings_store
from firmware.core.crypto.mac import verify_tag
from firmware.core.fonts import font_14, font_28
from firmware.core.message import decode, xor_pad
from firmware.core.widgets.button import Button
from firmware.core.widgets.keypad import Keypad, HEX_LAYOUT
from firmware.core.widgets.text import draw_text

_BG     = 0x0000
_FG     = 0xFFFF
_TEAL   = 0x0640
_GREY   = 0x8C71
_GREEN  = 0x07E0
_YELLOW = 0xFFE0
_RED    = 0xF800
_DIM    = 0x2945

_MAX_HEX     = 1028   # (4+2+500+8)*2
_HEX_CPL     = 39     # chars per line at font_14, x=4 (39 × 8 px = 312 px fits in 316 px)
_HEX_LINE_H  = 16
_INPUT_Y0    = 22
_INPUT_LINES = 9

_HEX_CHARS = frozenset('0123456789ABCDEF')


def _wrap(text: str) -> list:
    lines = []
    while text:
        lines.append(text[:_HEX_CPL])
        text = text[_HEX_CPL:]
    return lines or ['']


def _trial_decrypt(hal, hex_str: str, burn_after_reading: bool = False):
    """Try all contacts in canonical order. Returns (contact_id, name, plaintext, is_replay, error_str).
    contact_id/name/plaintext are None on error."""
    try:
        offset, ciphertext, tag = decode(hex_str)
    except ValueError as e:
        return None, None, None, False, "Invalid code format - check what you typed."

    n = len(ciphertext)
    for contact in contacts_store.list_contacts(hal):
        cid = contact["id"]
        if not contacts_store.pads_valid(hal, cid):
            continue
        pad_path = contacts_store.paths_for(cid)["pad_receive"]
        try:
            pad = hal.read_secret_slice(pad_path, offset, n + 8)
        except OSError:
            continue
        if not verify_tag(pad[n:], offset, n, ciphertext, tag):
            continue
        used_ranges = bookkeeping.read_used_ranges(hal, cid)
        replay = bookkeeping.is_replay(used_ranges, offset, offset + n + 8)
        try:
            plaintext = xor_pad(ciphertext, pad[:n]).decode("ascii")
        except (UnicodeDecodeError, ValueError):
            return cid, contact["name"], None, False, "Message contains unsupported characters."
        if not replay:
            bookkeeping.append_used_range(hal, cid, offset, offset + n + 8)
            if burn_after_reading:
                hal.overwrite_secret_slice(pad_path, offset, hal.get_random_bytes(n + 8))
        return cid, contact["name"], plaintext, replay, ""

    return None, None, None, False, "Couldn't decode - unknown sender."


def _show_empty_receive(hal, session):
    """No-contacts empty state. Returns 'ADDED', None (back), or 'LOCK'."""
    btn_add  = Button(40, 420, 240, 46, "Add a contact", font_14, _BG, _TEAL)
    btn_back = Button(4, 4, 60, 44, "<", font_14, _FG, _DIM)

    hal.fill_rect(0, 0, 320, 480, _BG)
    draw_text(hal, 104, 20, "Receive", font_28, _FG)
    draw_text(hal, 96, 160, "No contacts yet.", font_14, _GREY)
    draw_text(hal, 80, 180, "Add a contact first.", font_14, _GREY)
    btn_add.draw(hal)
    btn_back.draw(hal)

    while True:
        if session.is_idle_expired(hal):
            return "LOCK"
        hal.feed_watchdog()
        if hal.power_button_pressed():
            hal.power_off()
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        session.record_touch(hal)
        x, y = t
        if btn_back.hit_test(x, y):
            return None
        if btn_add.hit_test(x, y):
            from firmware.core.screens.contacts import ContactsScreen
            r = ContactsScreen(hal, session).run()
            if r == "LOCK":
                return "LOCK"
            return "RECHECK"


class PickInputMethodScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self):
        """Returns None (back to home) or 'LOCK'."""
        self._hal.notify_screen("PickInputMethod")
        breadcrumb.mark(self._hal, "PickInputMethod")
        hal = self._hal
        session = self._session

        while True:
            contacts = contacts_store.list_contacts(hal)

            if not contacts:
                r = _show_empty_receive(hal, session)
                if r == "LOCK":
                    return "LOCK"
                if r == "RECHECK":
                    continue
                return None

            btn_qr     = Button(40, 140, 240, 52, "Scan QR code", font_14, _BG, _TEAL)
            btn_manual = Button(40, 210, 240, 52, "Type it in manually", font_14, _FG, _DIM)
            btn_back   = Button(4, 4, 60, 44, "<", font_14, _FG, _DIM)
            error = [""]

            def draw():
                hal.fill_rect(0, 0, 320, 480, _BG)
                draw_text(hal, 104, 20, "Receive", font_28, _FG)
                btn_qr.draw(hal)
                btn_manual.draw(hal)
                btn_back.draw(hal)
                if error[0]:
                    draw_text(hal, 4, 330, error[0], font_14, _RED)

            draw()

            while True:
                if session.is_idle_expired(hal):
                    return "LOCK"
                hal.feed_watchdog()
                if hal.power_button_pressed():
                    hal.power_off()
                t = hal.get_touch()
                if t is None:
                    hal.feed_watchdog()
                    time.sleep(0.05)
                    continue
                session.record_touch(hal)
                x, y = t

                if btn_back.hit_test(x, y):
                    return None

                hex_str = None
                if btn_qr.hit_test(x, y):
                    r = _qr_flow(hal, session)
                    if r == "LOCK":
                        return "LOCK"
                    if r != "BACK":
                        hex_str = r
                elif btn_manual.hit_test(x, y):
                    r = _manual_hex_flow(hal, session)
                    if r == "LOCK":
                        return "LOCK"
                    if r != "BACK":
                        hex_str = r

                if hex_str is not None:
                    burn = settings_store.get_bool(hal, "burn_after_reading")
                    contact_id, name, plaintext, replay, err = _trial_decrypt(hal, hex_str, burn)
                    if err:
                        error[0] = err
                        draw()
                        continue
                    if not burn:
                        session.message_history.append({
                            "type": "received",
                            "text": plaintext,
                            "replay": replay,
                            "contact_id": contact_id,
                        })
                    r2 = ShowPlaintextScreen(hal, session, plaintext, replay, name).run()
                    if r2 == "LOCK":
                        return "LOCK"
                    return None

                draw()


def _qr_flow(hal, session):
    """Show QR scan screen. Returns hex string, 'BACK', or 'LOCK'."""
    btn_scan   = Button(80, 200, 160, 48, "Scan now", font_14, _BG, _TEAL)
    btn_cancel = Button(4, 380, 88, 32, "< Cancel", font_14, _FG, _DIM)

    def draw(status=""):
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 60, "QR Scanner", font_28, _FG)
        draw_text(hal, 4, 110, "Point at sender's screen,", font_14, _GREY)
        draw_text(hal, 4, 128, "then tap Scan.", font_14, _GREY)
        btn_scan.draw(hal)
        if status:
            draw_text(hal, 4, 270, status, font_14, _GREY)
        btn_cancel.draw(hal)

    draw()
    # Sim-only onboarding signal: the "tap Scan now" screen is showing.
    # No-op on real hardware (see base.py).
    hal.notify_screen("QRScanPrompt")

    while True:
        if session.is_idle_expired(hal):
            return "LOCK"
        hal.feed_watchdog()
        if hal.power_button_pressed():
            hal.power_off()
        t = hal.get_touch()
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)
            continue
        session.record_touch(hal)
        x, y = t
        if btn_cancel.hit_test(x, y):
            return "BACK"
        if btn_scan.hit_test(x, y):
            draw("Scanning...")
            hal.notify_screen("QRScanning")
            result = hal.qr_scan()
            if result is None:
                draw("Nothing detected - try again.")
            else:
                return result


def _manual_hex_flow(hal, session):
    """Hex ciphertext keyboard entry. Returns hex string, 'BACK', or 'LOCK'."""
    buf = []
    error = ""
    result = [None]
    done = [False]

    def draw_chrome():
        hal.fill_rect(0, 0, 320, 480, _BG)
        btn_back.draw(hal)
        kb.draw()

    def draw_input():
        hal.fill_rect(0, 0, 320, 240, _BG)
        draw_text(hal, 4, 4, f"Code  {len(buf)}/{_MAX_HEX}", font_14, _GREY)
        lines = _wrap(''.join(buf) + '_')
        for i, line in enumerate(lines[-_INPUT_LINES:]):
            draw_text(hal, 4, _INPUT_Y0 + i * _HEX_LINE_H, line, font_14, _FG)
        if error:
            draw_text(hal, 4, 170, error, font_14, _RED)
        btn_back.draw(hal)

    def on_char(ch):
        nonlocal error
        if len(buf) < _MAX_HEX and ch in _HEX_CHARS:
            buf.append(ch)
            error = ""
            draw_input()

    def on_back():
        if buf:
            buf.pop()
            draw_input()

    def on_done():
        nonlocal error
        if len(buf) < 28:
            error = "Too short - keep typing"
            draw_input()
            return
        if len(buf) % 2 != 0:
            error = "Invalid code - check for missing characters"
            draw_input()
            return
        result[0] = ''.join(buf)
        done[0] = True

    btn_back = Button(4, 196, 90, 44, "< Back", font_14, _FG, _DIM)
    kb = Keypad(hal, font_28, font_14, on_char, on_back, on_done, HEX_LAYOUT)
    draw_chrome()
    draw_input()

    while not done[0]:
        if session.is_idle_expired(hal):
            return "LOCK"
        hal.feed_watchdog()
        if hal.power_button_pressed():
            hal.power_off()
        t = hal.get_touch()
        if t is not None:
            session.record_touch(hal)
            if btn_back.hit_test(*t):
                return "BACK"
        kb.update(t)
        if t is None:
            hal.feed_watchdog()
            time.sleep(0.05)

    return result[0]


class ShowPlaintextScreen:

    def __init__(self, hal, session, plaintext: str, replay: bool, contact_name: str):
        self._hal = hal
        self._session = session
        self._plaintext = plaintext
        self._replay = replay
        self._contact_name = contact_name

    def run(self):
        """Returns None on done, 'LOCK' on auto-lock."""
        self._hal.notify_screen("ShowPlaintext")
        breadcrumb.mark(self._hal, "ShowPlaintext")
        hal = self._hal
        session = self._session

        btn_done = Button(80, 432, 160, 40, "Done", font_14, _BG, _TEAL)
        color = _YELLOW if self._replay else _GREEN

        hal.fill_rect(0, 0, 320, 480, _BG)
        y = 4
        draw_text(hal, 4, y, f"From: {self._contact_name[:34]}", font_14, color)
        y += 20
        if self._replay:
            draw_text(hal, 4, y, "Already decoded - received before.", font_14, _YELLOW)
            y += 20

        for line in _wrap(self._plaintext):
            if y + _HEX_LINE_H > 428:
                break
            draw_text(hal, 4, y, line, font_14, _FG)
            y += _HEX_LINE_H

        btn_done.draw(hal)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            session.record_touch(hal)
            if btn_done.hit_test(*t):
                return None
