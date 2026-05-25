import time

from firmware.core import breadcrumb
from firmware.core.bookkeeping import PAD_SIZE, read_watermark, advance_watermark
from firmware.core.contacts_store import paths_for
from firmware.core.crypto.mac import compute_tag
from firmware.core.fonts import font_14, font_28
from firmware.core.message import encode, xor_pad
from firmware.core.widgets.button import Button
from firmware.core.widgets.keyboard import Keyboard
from firmware.core.widgets.qr import draw_qr, fit_module_px, make_qr
from firmware.core.widgets.text import draw_text

_BG    = 0x0000
_FG    = 0xFFFF
_TEAL  = 0x0640
_GREY  = 0x8C71
_GREEN = 0x07E0
_RED   = 0xF800
_DIM   = 0x2945

_MAX_CHARS = 500

# ShowCiphertext QR geometry: module pixel size is computed at runtime so the
# QR fills as much of the available area as possible (smaller messages → larger
# QR). Width budget is the full 320 px screen; height budget is the space
# between _QR_Y and the top of the hex fallback area (303 px, preserving the
# v22-worst-case layout).
_QR_Y      = 20
_QR_MAX_W  = 320
_QR_MAX_H  = 303

_HEX_CPL        = 40   # hex chars per line at font_14, x=0 (40 × 8 px = 320 px)
_INPUT_CPL      = 39   # chars per line in the input area, x=4 (39 × 8 px = 312 px, fits in 316 px)
_HEX_LINE_H = 16
_DONE_BTN_Y = 432

# SendScreen input area
_INPUT_Y0  = 22
_INPUT_LINES = 12


def _wrap(text: str, cpl: int = _INPUT_CPL) -> list:
    lines = []
    while text:
        lines.append(text[:cpl])
        text = text[cpl:]
    return lines or ['']


class SendScreen:

    def __init__(self, hal, session, contact_id: str):
        self._hal = hal
        self._session = session
        self._contact_id = contact_id

    def run(self):
        """Returns hex ciphertext on send, None on back, 'LOCK' on auto-lock."""
        self._hal.notify_screen("Send")
        breadcrumb.mark(self._hal, "Send")
        hal = self._hal
        session = self._session
        contact_id = self._contact_id
        buf = []
        error = ""
        hex_result = [None]
        sent = [False]
        prev_lines = []
        prev_error = ""

        def draw_chrome():
            nonlocal prev_lines, prev_error
            hal.fill_rect(0, 0, 320, 480, _BG)
            btn_back.draw(hal)
            kb.draw()
            prev_lines = []
            prev_error = ""

        def draw_input():
            nonlocal prev_lines, prev_error
            cw = font_14.max_width()
            # Counter line always changes with each keystroke
            hal.fill_rect(0, 4, 320, _HEX_LINE_H, _BG)
            draw_text(hal, 4, 4, f"Message  {len(buf)}/{_MAX_CHARS}", font_14, _GREY)
            new_lines = _wrap(''.join(buf) + '_')[-_INPUT_LINES:]
            # Fast path: no reflow and last line changed by exactly ±1 char.
            # Only update the 1-2 character slots that actually changed - no black flash.
            fast = False
            if prev_lines and len(new_lines) == len(prev_lines):
                old_last = prev_lines[-1]
                new_last = new_lines[-1]
                diff = len(new_last) - len(old_last)
                y = _INPUT_Y0 + (len(new_lines) - 1) * _HEX_LINE_H
                if diff == 1:
                    # Append: draw the new char + cursor at their two slots
                    pos = len(new_last) - 2
                    draw_text(hal, 4 + pos * cw, y, new_last[pos:], font_14, _FG)
                    fast = True
                elif diff == -1:
                    # Delete: draw cursor one slot left, clear the vacated slot
                    pos = len(new_last) - 1
                    draw_text(hal, 4 + pos * cw, y, '_', font_14, _FG)
                    hal.fill_rect(4 + (pos + 1) * cw, y, cw, _HEX_LINE_H, _BG)
                    fast = True
            # Slow path: initial draw, line wrap/unroll, or other multi-char change
            if not fast:
                n_new = len(new_lines)
                n_old = len(prev_lines)
                for i in range(max(n_new, n_old)):
                    y = _INPUT_Y0 + i * _HEX_LINE_H
                    new_line = new_lines[i] if i < n_new else ''
                    old_line = prev_lines[i] if i < n_old else ''
                    if new_line != old_line:
                        hal.fill_rect(0, y, 320, _HEX_LINE_H, _BG)
                        if new_line:
                            draw_text(hal, 4, y, new_line, font_14, _FG)
            prev_lines = new_lines
            # Error line - only repaint when state changes
            if error != prev_error:
                hal.fill_rect(0, 218, 320, _HEX_LINE_H, _BG)
                if error:
                    draw_text(hal, 4, 218, error, font_14, _RED)
                prev_error = error

        def on_char(ch):
            nonlocal error
            if len(buf) < _MAX_CHARS:
                was_empty = not buf
                buf.append(ch)
                # Sim-only onboarding signal: the user has begun composing.
                # First keystroke only; no-op on real hardware (see base.py).
                if was_empty:
                    hal.notify_screen("Composing")
                error = ""
                draw_input()

        def on_back():
            if buf:
                buf.pop()
                draw_input()

        def on_done():
            nonlocal error
            if not buf:
                error = "Nothing to send"
                draw_input()
                return
            plaintext = ''.join(buf)
            n = len(plaintext)
            offset = read_watermark(hal, contact_id)
            if offset + n + 8 > PAD_SIZE:
                error = "No capacity left. Set up new keys."
                draw_input()
                return
            pad_path = paths_for(contact_id)["pad_send"]
            try:
                pad = hal.read_secret_slice(pad_path, offset, n + 8)
            except OSError:
                error = "No capacity left. Set up new keys."
                draw_input()
                return
            ciphertext = xor_pad(plaintext.encode("ascii"), pad[:n])
            tag = compute_tag(pad[n:], offset, n, ciphertext)
            advance_watermark(hal, contact_id, n + 8)
            hex_result[0] = encode(offset, ciphertext, tag)
            session.message_history.append({
                "type": "sent",
                "text": plaintext,
                "hex": hex_result[0],
                "contact_id": contact_id,
            })
            sent[0] = True

        btn_back = Button(4, 244, 90, 44, "< Back", font_14, _FG, _DIM)
        kb = Keyboard(hal, font_28, font_14, on_char, on_back, on_done)
        draw_chrome()
        draw_input()

        while not sent[0]:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                if btn_back.hit_test(*t):
                    return None
            kb.update(t)
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)

        return hex_result[0]


class ShowCiphertextScreen:

    def __init__(self, hal, session, hex_str: str):
        self._hal = hal
        self._session = session
        self._hex_str = hex_str

    def run(self):
        """Returns None on done, 'LOCK' on auto-lock."""
        self._hal.notify_screen("ShowCiphertext")
        breadcrumb.mark(self._hal, "ShowCiphertext")
        hal = self._hal
        session = self._session
        hex_str = self._hex_str

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 4, "Preparing...", font_14, _GREY)
        matrix = make_qr(hex_str)

        n_modules = len(matrix)
        module_px = fit_module_px(n_modules, _QR_MAX_W, _QR_MAX_H)
        qr_size_px = n_modules * module_px
        qr_x = (320 - qr_size_px) // 2
        hex_y0 = _QR_Y + qr_size_px + 4

        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 4, "Encoded message", font_14, _GREEN)
        draw_qr(hal, matrix, qr_x, _QR_Y, module_px)
        hal.notify_qr(hex_str)

        n_lines = (_DONE_BTN_Y - hex_y0) // _HEX_LINE_H
        for i in range(n_lines):
            chunk = hex_str[i * _HEX_CPL : (i + 1) * _HEX_CPL]
            if not chunk:
                break
            draw_text(hal, 0, hex_y0 + i * _HEX_LINE_H, chunk, font_14, _GREY)

        btn_done = Button(80, _DONE_BTN_Y, 160, 40, "Done", font_14, _BG, _TEAL)
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
