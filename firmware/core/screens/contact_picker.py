import time

from firmware.core import breadcrumb, contacts_store
from firmware.core.bookkeeping import PAD_SIZE, read_watermark
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.text import draw_text

_BG   = 0x0000
_FG   = 0xFFFF
_TEAL = 0x0640
_GREY = 0x8C71
_DIM  = 0x2945

_ROW_H   = 52
_LIST_Y0 = 80
_LIST_Y1 = 392


class ContactPickerScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self):
        """Returns contact_id if selected, None on back, 'LOCK' on auto-lock."""
        self._hal.notify_screen("ContactPicker")
        breadcrumb.mark(self._hal, "ContactPicker")
        hal = self._hal
        session = self._session

        while True:
            contacts = contacts_store.list_contacts(hal)
            rows = [(c["id"], c["name"], contacts_store.pads_valid(hal, c["id"])) for c in contacts]

            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 88, 20, "Send to...", font_28, _FG)
            btn_back = Button(4, 4, 60, 44, "<", font_14, _FG, _DIM)
            btn_back.draw(hal)

            btn_add = None
            if not rows:
                draw_text(hal, 96, 160, "No contacts yet.", font_14, _GREY)
                draw_text(hal, 80, 180, "Add a contact first.", font_14, _GREY)
                btn_add = Button(40, 420, 240, 46, "Add a contact", font_14, _BG, _TEAL)
                btn_add.draw(hal)
            else:
                for i, (cid, name, valid) in enumerate(rows):
                    ry = _LIST_Y0 + i * _ROW_H
                    if ry >= _LIST_Y1:
                        break
                    self._draw_row(ry, cid, name, valid)

            action = None
            while action is None:
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

                if btn_add is not None and btn_add.hit_test(x, y):
                    action = "ADD"
                    continue

                for i, (cid, name, valid) in enumerate(rows):
                    ry = _LIST_Y0 + i * _ROW_H
                    if ry >= _LIST_Y1:
                        break
                    if valid and ry <= y < ry + _ROW_H and 4 <= x <= 316:
                        action = ("SELECT", cid)
                        break

            if action == "ADD":
                from firmware.core.screens.contacts import ContactsScreen
                r = ContactsScreen(hal, session).run()
                if r == "LOCK":
                    return "LOCK"
                # Re-loop to show updated contact list
            elif isinstance(action, tuple):
                return action[1]

    def _draw_row(self, y: int, contact_id: str, name: str, valid: bool) -> None:
        hal = self._hal
        self._hal.fill_rect(4, y, 312, _ROW_H - 4, _DIM)
        draw_text(hal, 10, y + 6, name[:22], font_14, _FG if valid else _GREY)
        if valid:
            watermark = read_watermark(hal, contact_id)
            pct = int((PAD_SIZE - watermark) * 100 / PAD_SIZE)
            draw_text(hal, 10, y + 26, f"Capacity {pct}% remaining", font_14, _GREY)
        else:
            draw_text(hal, 10, y + 26, "Setup interrupted", font_14, _GREY)
