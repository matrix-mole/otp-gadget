import time

from firmware.core import breadcrumb
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.gear_button import GearButton
from firmware.core.widgets.lock_button import LockButton
from firmware.core.widgets.text import draw_text, draw_text_centered, text_width

_BG     = 0x0000
_FG     = 0xFFFF
_TEAL   = 0x0640
_GREY   = 0x8C71
_DIM    = 0x2945
_YELLOW = 0xFFE0  # USB / lightning colour

_STATUS_POLL_MS = 5_000  # re-check battery/USB every 5 s while idle

# 8×14 lightning bolt icon (same approach as lock_button.py).
# '1' = _YELLOW pixel, '0' = _BG pixel.
_BOLT_W = 8
_BOLT_H = 14
_BOLT_LAYOUT = (
    "00110000",
    "01110000",
    "11110000",
    "11100000",
    "11111110",
    "11111100",
    "00111110",
    "00011110",
    "00011100",
    "00001100",
    "00001000",
    "00001000",
    "00000000",
    "00000000",
)
_bolt_buf = bytearray(_BOLT_W * _BOLT_H * 2)
_bolt_idx = 0
for _bolt_row in _BOLT_LAYOUT:
    for _bolt_ch in _bolt_row:
        _c = _YELLOW if _bolt_ch == "1" else _BG
        _bolt_buf[_bolt_idx]     = (_c >> 8) & 0xFF
        _bolt_buf[_bolt_idx + 1] = _c & 0xFF
        _bolt_idx += 2
_BOLT_ICON = bytes(_bolt_buf)
del _bolt_buf, _bolt_idx, _bolt_row, _bolt_ch, _c


class HomeScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self) -> str:
        """Returns 'LOCK' on auto-lock timeout."""
        hal = self._hal
        session = self._session

        btn_send     = Button(40, 220, 240, 46, "Send message",    font_14, _BG, _TEAL)
        btn_recv     = Button(40, 280, 240, 46, "Receive message",  font_14, _BG, _TEAL)
        btn_contacts = Button(40, 340, 240, 46, "Contacts",         font_14, _BG, _TEAL)
        btn_gear     = GearButton(270, 10, 40, 36)
        btn_lock     = LockButton(10, 10, 40, 36)

        self._draw(btn_send, btn_recv, btn_contacts, btn_gear, btn_lock)

        last_batt = hal.battery_status()
        last_poll = hal.ticks_ms()

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()

            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                now = hal.ticks_ms()
                if now - last_poll >= _STATUS_POLL_MS:
                    last_poll = now
                    cur_batt = hal.battery_status()
                    if cur_batt != last_batt:
                        last_batt = cur_batt
                        self._draw_status(cur_batt, btn_gear, btn_lock)
                time.sleep(0.05)
                continue

            session.record_touch(hal)
            x, y = t

            if btn_lock.hit_test(x, y):
                return "LOCK"
            elif btn_send.hit_test(x, y):
                from firmware.core.screens.contact_picker import ContactPickerScreen
                from firmware.core.screens.send import SendScreen, ShowCiphertextScreen
                contact_id = ContactPickerScreen(hal, session).run()
                if contact_id == "LOCK":
                    return "LOCK"
                if isinstance(contact_id, str):
                    hex_str = SendScreen(hal, session, contact_id).run()
                    if hex_str == "LOCK":
                        return "LOCK"
                    if isinstance(hex_str, str):
                        r = ShowCiphertextScreen(hal, session, hex_str).run()
                        if r == "LOCK":
                            return "LOCK"
                self._draw(btn_send, btn_recv, btn_contacts, btn_gear, btn_lock)
            elif btn_recv.hit_test(x, y):
                from firmware.core.screens.receive import PickInputMethodScreen
                r = PickInputMethodScreen(hal, session).run()
                if r == "LOCK":
                    return "LOCK"
                self._draw(btn_send, btn_recv, btn_contacts, btn_gear, btn_lock)
            elif btn_contacts.hit_test(x, y):
                from firmware.core.screens.contacts import ContactsScreen
                r = ContactsScreen(hal, session).run()
                if r == "LOCK":
                    return "LOCK"
                self._draw(btn_send, btn_recv, btn_contacts, btn_gear, btn_lock)
            elif btn_gear.hit_test(x, y):
                from firmware.core.screens.settings import SettingsScreen
                r = SettingsScreen(hal, session).run()
                if r == "LOCK":
                    return "LOCK"
                self._draw(btn_send, btn_recv, btn_contacts, btn_gear, btn_lock)

    def _draw(self, btn_send, btn_recv, btn_contacts, btn_gear, btn_lock) -> None:
        self._hal.notify_screen("Home")
        breadcrumb.mark(self._hal, "Home")
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        self._draw_status(hal.battery_status(), btn_gear, btn_lock)
        btn_send.draw(hal)
        btn_recv.draw(hal)
        btn_contacts.draw(hal)

    def _draw_status(self, batt, btn_gear, btn_lock) -> None:
        pct, charging, vbus = batt
        hal = self._hal
        hal.fill_rect(0, 0, 320, 40, _BG)
        if vbus:
            label = f"USB: {pct}%"
            gap = 2
            total_w = _BOLT_W + gap + text_width(label, font_14)
            x0 = max(0, (320 - total_w) // 2)
            hal.blit_rect(x0, 20, _BOLT_W, _BOLT_H, _BOLT_ICON)
            draw_text(hal, x0 + _BOLT_W + gap, 20, label, font_14, _YELLOW)
        else:
            draw_text_centered(hal, 20, f"Bat: {pct}%", font_14, _GREY)
        btn_gear.draw(hal)
        btn_lock.draw(hal)
