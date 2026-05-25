import time

from firmware.core import breadcrumb, bookkeeping
from firmware.core import contacts_store
from firmware.core.bookkeeping import PAD_SIZE
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.keyboard import Keyboard
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG   = 0x0000
_FG   = 0xFFFF
_TEAL = 0x0640
_GREY = 0x8C71
_DIM  = 0x2945
_RED  = 0xF800

_MAX_NAME    = 32
_ROW_H       = 52    # px per contact row
_LIST_Y0     = 80    # y where the list starts (below title)
_LIST_Y1     = 392   # y where the list ends (6 rows × 52 px = 312 px + 80)
_SUBLABEL_H  = 18    # px reserved for a section sub-title
_SECTION_GAP = 10    # extra vertical gap between sections


# ── Helpers ──────────────────────────────────────────────────────────────────

def _text_width(text, font) -> int:
    return sum(font.get_ch(ch)[2] for ch in text)


def _send_remaining(hal, contact_id: str) -> int:
    return PAD_SIZE - bookkeeping.read_watermark(hal, contact_id)


def _receive_remaining(hal, contact_id: str) -> int:
    used = sum(e - s for s, e in bookkeeping.read_used_ranges(hal, contact_id))
    return max(0, PAD_SIZE - used)


def _row_status(hal, contact_id: str, in_flight) -> str:
    """Status string for a committed contact row."""
    if (
        in_flight
        and in_flight["id"] == contact_id
        and in_flight["kind"] == "reexchange"
    ):
        if hal.file_exists("own", "/exchange/OTP.bin"):
            return "Ready to complete"
        if hal.file_exists("own", "/exchange/X_own.bin"):
            return "Waiting..."
        return "Pending"
    return "Ready" if contacts_store.pads_valid(hal, contact_id) else "Setup interrupted"


def _pending_add_status(hal) -> str:
    """Status badge for the synthetic pending-add row."""
    if hal.file_exists("own", "/exchange/OTP.bin"):
        return "Ready to complete"
    if hal.file_exists("own", "/exchange/X_own.bin"):
        return "Waiting..."
    return "Pending"


def _wipe_contact_pads(hal, contact_id: str) -> None:
    """Delete all 4 per-contact pad/bookkeeping files and any own-card staging."""
    for path in contacts_store.paths_for(contact_id).values():
        if hal.file_exists("own", path):
            hal.delete_file("own", path)
    for path in ("/exchange/X_own.bin", "/exchange/OTP.bin"):
        if hal.file_exists("own", path):
            hal.delete_file("own", path)


def _route_to_exchange(hal, session, contact_id: str) -> str:
    if hal.mount_card("guest") == "MOUNTED" and hal.file_exists("guest", "/exchange/X_own.bin"):
        from firmware.core.screens.exchange import FinalizeExchangeScreen
        return FinalizeExchangeScreen(hal, session, contact_id).run()
    from firmware.core.screens.exchange import PrepareExchangeScreen
    return PrepareExchangeScreen(hal, session, contact_id).run()



# ── Screens ──────────────────────────────────────────────────────────────────

class ContactsScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self) -> str:
        """Returns 'HOME' or 'LOCK'."""
        hal = self._hal
        session = self._session

        while True:
            self._hal.notify_screen("Contacts")
            breadcrumb.mark(self._hal, "Contacts")
            contacts  = contacts_store.list_contacts(hal)
            in_flight = contacts_store.get_in_flight(hal)

            # ── Draw ──────────────────────────────────────────────────────
            hal.fill_rect(0, 0, 320, 480, _BG)
            title = "Contacts"
            tx = (320 - _text_width(title, font_28)) // 2
            draw_text(hal, tx, 20, title, font_28, _FG)
            btn_back = Button(4, 4, 60, 44, "<", font_14, _FG, _DIM)
            btn_back.draw(hal)

            row_ys = []  # [(cid, name, status, y)] - used by hit-test below
            cy = _LIST_Y0

            # "New contacts in-process" section - only when an add is in-flight
            if in_flight and in_flight["kind"] == "add":
                draw_text(hal, 4, cy, "Contacts being set up", font_14, _GREY)
                cy += _SUBLABEL_H
                cid  = in_flight["id"]
                name = in_flight["name"]
                status = _pending_add_status(hal)
                row_ys.append((cid, name, status, cy))
                self._draw_row(cy, cid, name, status)
                cy += _ROW_H

            # "Existing contacts" section - always shown
            if cy > _LIST_Y0:
                cy += _SECTION_GAP
            draw_text(hal, 4, cy, "Existing contacts", font_14, _GREY)
            cy += _SUBLABEL_H
            if contacts:
                for c in contacts:
                    if cy >= _LIST_Y1:
                        break
                    cid    = c["id"]
                    name   = c["name"]
                    status = _row_status(hal, cid, in_flight)
                    row_ys.append((cid, name, status, cy))
                    self._draw_row(cy, cid, name, status)
                    cy += _ROW_H
            else:
                cy += 8
                no_tx = (320 - _text_width("You have no contacts", font_14)) // 2
                draw_text(hal, no_tx, cy, "You have no contacts", font_14, _GREY)

            btn_add = Button(40, 394, 240, 36, "+ Add contact", font_14, _BG, _TEAL)
            btn_add.draw(hal)

            # ── Input ─────────────────────────────────────────────────────
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

                for cid, name, status, ry in row_ys:
                    if ry <= y < ry + _ROW_H and 4 <= x <= 316:
                        action = ("ROW", cid, name, status)
                        break

                if action is None:
                    if btn_back.hit_test(x, y):
                        action = "HOME"
                    elif btn_add.hit_test(x, y):
                        action = "ADD" if in_flight is None else "BLOCKED"

            if action == "HOME":
                return "HOME"
            elif action == "ADD":
                r = AddContactScreen(hal, session).run()
                if r == "LOCK":
                    return "LOCK"
            elif action == "BLOCKED":
                r = self._blocked_modal(in_flight["name"], session)
                if r == "LOCK":
                    return "LOCK"
            elif isinstance(action, tuple):
                _, cid, name, status = action
                if status == "Ready":
                    from firmware.core.screens.contact_thread import ContactThreadScreen
                    r = ContactThreadScreen(hal, session, cid, name).run()
                else:
                    r = ContactDetailScreen(hal, session, cid, name).run()
                if r == "LOCK":
                    return "LOCK"
            # re-loop to redraw with fresh state

    def _blocked_modal(self, inf_name: str, session) -> str:
        """Show a fullscreen modal explaining why + Add contact is blocked.
        Returns 'LOCK' on auto-lock, 'OK' on dismiss."""
        hal = self._hal
        _MW, _MH = 304, 456
        _MX = (320 - _MW) // 2   # 8
        _MY = (480 - _MH) // 2   # 12
        _BD = 2                   # border thickness

        hal.fill_rect(_MX, _MY, _MW, _MH, _BG)
        hal.fill_rect(_MX,             _MY,             _MW, _BD,  _TEAL)
        hal.fill_rect(_MX,             _MY + _MH - _BD, _MW, _BD,  _TEAL)
        hal.fill_rect(_MX,             _MY,             _BD, _MH,  _TEAL)
        hal.fill_rect(_MX + _MW - _BD, _MY,             _BD, _MH,  _TEAL)

        lines = [
            "Only one contact can be",
            "added at a time.",
            "",
            "Finish (or cancel) adding",
            f"{inf_name[:20]} as a contact.",
        ]
        line_h = 18
        text_h = len(lines) * line_h
        btn_h  = 46
        text_y0 = _MY + (_MH - text_h - btn_h - 16) // 2
        for i, line in enumerate(lines):
            if line:
                lw = _text_width(line, font_14)
                draw_text(hal, _MX + (_MW - lw) // 2, text_y0 + i * line_h, line, font_14, _FG)

        btn_ok = Button(_MX + (_MW - 120) // 2, _MY + _MH - btn_h - 16, 120, btn_h, "OK", font_14, _BG, _TEAL)
        btn_ok.draw(hal)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                x, y = t
                if btn_ok.hit_test(x, y):
                    return "OK"
            hal.feed_watchdog()
            time.sleep(0.05)

    def _draw_row(self, y: int, contact_id: str, name: str, status: str) -> None:
        hal = self._hal
        hal.fill_rect(4, y, 312, _ROW_H - 4, _DIM)
        draw_text(hal, 10, y + 6, name[:22], font_14, _FG, _DIM)
        if status == "Ready":
            send_pct = int(_send_remaining(hal, contact_id) * 100 / PAD_SIZE)
            recv_pct = int(_receive_remaining(hal, contact_id) * 100 / PAD_SIZE)
            draw_text(hal, 10, y + 26,
                      f"Send {send_pct}%  Receive {recv_pct}%",
                      font_14, _GREY, _DIM)
        else:
            draw_text(hal, 10, y + 26, status, font_14, _TEAL, _DIM)


class AddContactScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self) -> str:
        """Returns 'CONTACTS' or 'LOCK'."""
        self._hal.notify_screen("AddContact")
        breadcrumb.mark(self._hal, "AddContact")
        hal = self._hal
        session = self._session

        # Block if another exchange is already in flight
        in_flight = contacts_store.get_in_flight(hal)
        if in_flight is not None:
            inf_name = in_flight["name"][:20]
            r = self._blocked(f"Still setting up {inf_name}.\nFinish or cancel that first.")
            return "LOCK" if r == "LOCK" else "CONTACTS"

        buf = []
        done = [False]
        name_error = [""]
        _prev = ['', '']  # [prev_display, prev_error]

        def draw_chrome():
            hal.fill_rect(0, 0, 320, 480, _BG)
            draw_text(hal, 4, 20, "Contact name", font_28, _FG)
            btn_back.draw(hal)
            kb.draw()
            _prev[0] = ''
            _prev[1] = ''

        def draw_input():
            cw = font_14.max_width()
            display = "".join(buf)
            prev_display = _prev[0]
            # Text line: fast path for ±1 char when both states are non-empty
            if prev_display and display and abs(len(display) - len(prev_display)) == 1:
                if len(display) > len(prev_display):
                    draw_text(hal, 4 + len(prev_display) * cw, 90, display[-1], font_14, _FG)
                else:
                    hal.fill_rect(4 + len(display) * cw, 90, cw, 16, _BG)
            else:
                hal.fill_rect(0, 90, 320, 16, _BG)
                if display:
                    draw_text(hal, 4, 90, display, font_14, _FG)
                else:
                    draw_text(hal, 4, 90, '_', font_14, _GREY)
            _prev[0] = display
            # Counter line - always changes, clear and redraw
            hal.fill_rect(0, 120, 320, 16, _BG)
            draw_text(hal, 4, 120, f"{len(display)}/{_MAX_NAME}", font_14, _GREY)
            # Error line - only repaint when changed
            if name_error[0] != _prev[1]:
                hal.fill_rect(0, 138, 320, 16, _BG)
                if name_error[0]:
                    draw_text(hal, 4, 138, name_error[0][:36], font_14, _RED)
                _prev[1] = name_error[0]

        def on_char(ch):
            if len(buf) < _MAX_NAME:
                buf.append(ch)
                hal.notify_screen(f"ContactName:{''.join(buf).strip()}")
                name_error[0] = ""
                draw_input()

        def on_backspace():
            if buf:
                buf.pop()
                hal.notify_screen(f"ContactName:{''.join(buf).strip()}")
                draw_input()

        def on_done():
            candidate = "".join(buf).strip()
            if not candidate:
                name_error[0] = "Name cannot be empty"
                draw_input()
                return
            existing_id = contacts_store.find_by_name_ci(hal, candidate)
            if existing_id is not None:
                name_error[0] = f"{candidate[:16]} already exists"
                draw_input()
                return
            done[0] = True

        kb = Keyboard(hal, font_28, font_14, on_char, on_backspace, on_done)
        btn_back = Button(4, 244, 90, 44, "< Back", font_14, _FG, _DIM)
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
                    return "CONTACTS"
            kb.update(t)
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)

        name = "".join(buf).strip()
        contact_id = hal.get_random_bytes(4).hex()
        contacts_store.set_in_flight(hal, {
            "id": contact_id,
            "name": name,
            "started_at": hal.rtc_now(),
            "kind": "add",
        })
        r = _route_to_exchange(hal, session, contact_id)
        return "LOCK" if r == "LOCK" else "CONTACTS"

    def _blocked(self, msg: str) -> str:
        """Returns 'LOCK' on auto-lock, 'BACK' on back tap."""
        hal = self._hal
        session = self._session
        btn_back = Button(110, 320, 100, 44, "Back", font_14, _FG, _DIM)
        hal.fill_rect(0, 0, 320, 480, _BG)
        for i, line in enumerate(msg.split("\n")):
            draw_text(hal, 4, 180 + i * 20, line, font_14, _FG)
        btn_back.draw(hal)
        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                x, y = t
                if btn_back.hit_test(x, y):
                    return "BACK"
            hal.feed_watchdog()
            time.sleep(0.05)


class ContactDetailScreen:

    def __init__(self, hal, session, contact_id: str, name: str):
        self._hal = hal
        self._session = session
        self._contact_id = contact_id
        self._name = name

    def run(self) -> str:
        """Returns 'CONTACTS' or 'LOCK'."""
        hal = self._hal
        session = self._session

        while True:
            self._hal.notify_screen("ContactDetail")
            breadcrumb.mark(self._hal, "ContactDetail")
            status    = self._status()
            in_flight = contacts_store.get_in_flight(hal)
            is_add_waiting = (
                status in ("Waiting...", "Pending")
                and in_flight is not None
                and in_flight["kind"] == "add"
                and in_flight["id"] == self._contact_id
            )

            btn_back    = Button(4, 4, 60, 44, "<", font_14, _FG, _DIM)
            action_btns = {}

            if status == "Ready to complete":
                action_btns["fin"]     = Button(40, 200, 240, 46, "Complete setup", font_14, _BG, _TEAL)
                action_btns["discard"] = Button(40, 260, 240, 36, "Abandon",        font_14, _FG, _DIM)
            elif is_add_waiting:
                label = f"Cancel adding {self._name[:14]} as contact"
                action_btns["discard"] = Button(4, 420, 312, 40, label, font_14, _RED, _DIM)
            elif status in ("Waiting...", "Pending"):
                action_btns["discard"] = Button(40, 200, 240, 46, "Abandon", font_14, _FG, _DIM)
            elif status == "Ready":
                action_btns["new_msg"]  = Button(40, 298, 240, 46, "New message",               font_14, _BG,  _TEAL)
                action_btns["reex"]     = Button(40, 352, 240, 36, "Set up new keys",  font_14, _FG,  _DIM)
                action_btns["clr_data"] = Button(40, 396, 240, 36, "Delete keys only", font_14, _RED, _DIM)
                action_btns["del"]      = Button(40, 440, 240, 36, "Delete contact",             font_14, _RED, _DIM)
            elif status == "Setup interrupted":
                action_btns["reex"] = Button(40, 200, 240, 46, "Restart key setup", font_14, _BG, _TEAL)
                action_btns["del"]  = Button(40, 260, 240, 36, "Delete contact",      font_14, _RED, _DIM)

            self._draw(status, action_btns, btn_back, is_add_waiting)

            tapped = None
            while tapped is None:
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
                for key, btn in action_btns.items():
                    if btn.hit_test(x, y):
                        tapped = key
                        break
                if tapped is None and btn_back.hit_test(x, y):
                    tapped = "back"

            if tapped == "back":
                return "BACK"

            r = self._handle_action(tapped)
            if r in ("LOCK", "CONTACTS", "BACK"):
                return r
            # r == "STAY" → re-render with fresh state

    def _status(self) -> str:
        hal = self._hal
        cid = self._contact_id
        in_flight = contacts_store.get_in_flight(hal)
        if in_flight and in_flight["id"] == cid:
            if hal.file_exists("own", "/exchange/OTP.bin"):
                return "Ready to complete"
            if hal.file_exists("own", "/exchange/X_own.bin"):
                return "Waiting..."
            return "Pending"
        return "Ready" if contacts_store.pads_valid(hal, cid) else "Setup interrupted"

    def _handle_action(self, key: str) -> str:
        hal = self._hal
        session = self._session
        cid = self._contact_id
        name = self._name

        if key == "new_msg":
            from firmware.core.screens.send import SendScreen, ShowCiphertextScreen
            r = SendScreen(hal, session, cid).run()
            if r == "LOCK":
                return "LOCK"
            if r is not None:
                r2 = ShowCiphertextScreen(hal, session, r).run()
                if r2 == "LOCK":
                    return "LOCK"
            return "STAY"

        if key == "fin":
            from firmware.core.screens.exchange import FinalizeExchangeScreen
            r = FinalizeExchangeScreen(hal, session, cid).run()
            return "LOCK" if r == "LOCK" else "CONTACTS"

        if key == "discard":
            in_flight = contacts_store.get_in_flight(hal)
            is_add = in_flight and in_flight["kind"] == "add" and in_flight["id"] == cid
            if is_add:
                r = self._confirm_modal(
                    f"Are you sure you want to\ncancel adding {name[:16]}\nas a contact?"
                )
            else:
                r = self._confirm(f"Abandon setup with {name[:14]}?")
            if r == "LOCK":
                return "LOCK"
            if r == "NO":
                return "STAY"
            try:
                contacts_store.set_in_flight(hal, None)
                for path in ("/exchange/X_own.bin", "/exchange/OTP.bin"):
                    if hal.file_exists("own", path):
                        hal.delete_file("own", path)
                if in_flight and in_flight["kind"] == "add":
                    for path in contacts_store.paths_for(cid).values():
                        if hal.file_exists("own", path):
                            hal.delete_file("own", path)
            except OSError as e:
                self._message(f"SD card error ({e}).\nRestart the device\nand try again.")
                return "STAY"
            return "CONTACTS"

        if key == "reex":
            in_flight = contacts_store.get_in_flight(hal)
            if in_flight is not None and in_flight["id"] != cid:
                self._message(f"Finish setup with\n{in_flight['name'][:14]} first.")
                return "STAY"
            r = self._confirm_modal(
                f"Set up new keys with {name[:10]}?\n"
                "This deletes the current keys.\n"
                "You won't be able to message\n"
                f"{name[:10]} until you meet and\n"
                "complete the new key setup."
            )
            if r == "LOCK":
                return "LOCK"
            if r == "NO":
                return "STAY"
            try:
                _wipe_contact_pads(hal, cid)
                contacts_store.set_in_flight(hal, {
                    "id": cid,
                    "name": name,
                    "started_at": hal.rtc_now(),
                    "kind": "reexchange",
                })
            except OSError as e:
                self._message(f"SD card error ({e}).\nRestart the device\nand try again.")
                return "STAY"
            r = _route_to_exchange(hal, session, cid)
            return "LOCK" if r == "LOCK" else "CONTACTS"

        if key == "clr_data":
            r = self._confirm_modal(
                f"Delete keys for {name[:14]}?\n"
                "The contact stays, but you\n"
                "cannot message them until\n"
                "you set up new keys."
            )
            if r == "LOCK":
                return "LOCK"
            if r == "NO":
                return "STAY"
            try:
                for path in contacts_store.paths_for(cid).values():
                    if hal.file_exists("own", path):
                        hal.delete_file("own", path)
            except OSError as e:
                self._message(f"SD card error ({e}).\nRestart the device\nand try again.")
                return "STAY"
            return "CONTACTS"

        if key == "del":
            r = self._confirm_modal(
                f"Delete {name[:14]}?\n"
                "All keys and message history\n"
                f"for {name[:10]} will be deleted.\n"
                "You won't be able to\n"
                "message them after this."
            )
            if r == "LOCK":
                return "LOCK"
            if r == "NO":
                return "STAY"
            try:
                contacts_store.delete_contact(hal, cid)
                for path in contacts_store.paths_for(cid).values():
                    if hal.file_exists("own", path):
                        hal.delete_file("own", path)
            except OSError as e:
                self._message(f"SD card error ({e}).\nRestart the device\nand try again.")
                return "STAY"
            return "CONTACTS"

        return "STAY"

    def _confirm(self, msg: str) -> str:
        """Returns 'YES', 'NO', or 'LOCK'."""
        hal = self._hal
        session = self._session
        btn_yes = Button(40, 270, 110, 46, "Continue", font_14, _BG, _TEAL)
        btn_no  = Button(170, 270, 110, 46, "Cancel",   font_14, _FG, _DIM)
        hal.fill_rect(0, 0, 320, 480, _BG)
        for i, line in enumerate(msg.split("\n")):
            draw_text_centered(hal, 196 + i * 20, line, font_14, _FG)
        btn_yes.draw(hal)
        btn_no.draw(hal)
        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                x, y = t
                if btn_yes.hit_test(x, y):
                    return "YES"
                if btn_no.hit_test(x, y):
                    return "NO"
            hal.feed_watchdog()
            time.sleep(0.05)

    def _message(self, msg: str) -> None:
        hal = self._hal
        session = self._session
        hal.fill_rect(0, 0, 320, 480, _BG)
        for i, line in enumerate(msg.split("\n")):
            draw_text(hal, 4, 196 + i * 20, line, font_14, _FG)
        draw_text(hal, 4, 260, "Tap to continue", font_14, _GREY)
        while True:
            if session.is_idle_expired(hal):
                return
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                return
            hal.feed_watchdog()
            time.sleep(0.05)

    def _confirm_modal(self, msg: str) -> str:
        """Green-border fullscreen confirm. Returns 'YES', 'NO', or 'LOCK'."""
        hal     = self._hal
        session = self._session
        _MW, _MH = 304, 456
        _MX = (320 - _MW) // 2
        _MY = (480 - _MH) // 2
        _BD = 2

        hal.fill_rect(_MX, _MY, _MW, _MH, _BG)
        hal.fill_rect(_MX,             _MY,             _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY + _MH - _BD, _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY,             _BD, _MH, _TEAL)
        hal.fill_rect(_MX + _MW - _BD, _MY,             _BD, _MH, _TEAL)

        lines  = msg.split("\n")
        line_h = 18
        btn_h  = 46
        text_h = len(lines) * line_h
        text_y0 = _MY + (_MH - text_h - btn_h - 24) // 2
        for i, line in enumerate(lines):
            if line:
                lw = _text_width(line, font_14)
                draw_text(hal, _MX + (_MW - lw) // 2, text_y0 + i * line_h, line, font_14, _FG)

        bw    = (_MW - 48) // 2   # each button width (two side by side with gap)
        by    = _MY + _MH - btn_h - 16
        btn_yes = Button(_MX + 16,          by, bw, btn_h, "Continue", font_14, _BG, _TEAL)
        btn_no  = Button(_MX + 16 + bw + 8, by, bw, btn_h, "Cancel",   font_14, _FG,  _DIM)
        btn_yes.draw(hal)
        btn_no.draw(hal)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                x, y = t
                if btn_yes.hit_test(x, y):
                    return "YES"
                if btn_no.hit_test(x, y):
                    return "NO"
            hal.feed_watchdog()
            time.sleep(0.05)

    def _draw(self, status: str, action_btns: dict, btn_back,
              is_add_waiting: bool = False) -> None:
        hal = self._hal
        cid = self._contact_id
        hal.fill_rect(0, 0, 320, 480, _BG)

        if is_add_waiting:
            title = "New contact"
            tx = (320 - _text_width(title, font_28)) // 2
            draw_text(hal, tx, 20, title, font_28, _FG)
            name_w = _text_width(self._name[:20], font_14)
            draw_text(hal, (320 - name_w) // 2, 56, self._name[:20], font_14, _GREY)

            steps = [
                "Read all steps FIRST.",
                "",
                "1. Take out your memory card on",
                "   the right side.",
                "2. Give your memory card to",
                "   your partner. They should",
                "   insert it in the front slot",
                "   of the gadget (not the side).",
                "3. Your partner adds you as a",
                "   contact on their gadget.",
                "4. Your partner returns your",
                "   memory card. Insert it on",
                "   the left.",
                "5. DONE",
            ]
            sy = 90
            for line in steps:
                if line:
                    color = _TEAL if line == "Read all steps FIRST." else _FG
                    draw_text(hal, 4, sy, line, font_14, color)
                sy += 18
        else:
            draw_text(hal, 4, 54, self._name[:20], font_28, _FG)
            if status != "Ready":
                draw_text(hal, 4, 90, f"Status: {status}", font_14, _GREY)
            if status == "Ready":
                send_pct = int(_send_remaining(hal, cid) * 100 / PAD_SIZE)
                recv_pct = int(_receive_remaining(hal, cid) * 100 / PAD_SIZE)
                draw_text(hal, 4, 90, f"Send: {send_pct}%  Receive: {recv_pct}%", font_14, _GREY)

        for btn in action_btns.values():
            btn.draw(hal)
        btn_back.draw(hal)
