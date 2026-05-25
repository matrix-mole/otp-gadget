import time

from firmware.core import breadcrumb
from firmware.core.bookkeeping import PAD_SIZE, read_watermark, read_used_ranges
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.gear_button import GearButton
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG     = 0x0000
_FG     = 0xFFFF
_TEAL   = 0x0640
_GREEN  = 0x07E0
_YELLOW = 0xFFE0
_GREY   = 0x8C71
_DIM    = 0x2945

_LINES_PER_PAGE = 13   # message-area height: y=130 → pagination at y=350
_LINE_H         = 16
_ITEMS_Y0       = 130
_PREFIX_W       = 24   # 3 monospace chars × 8 px
_MSG_CPL        = 36   # message chars per line, x=28 (28 + 36 × 8 px = 316 ≤ 320)
_BAR_X          = 104 # x where the outlined bar starts; leaves room for "Receive 100%" (12 chars × 8 px = 96 px from x=4)
_BAR_W          = 212 # _BAR_X + _BAR_W + 4 (right margin) = 320
_BAR_H          = 16


def _total_pages(n_lines: int) -> int:
    return max(1, (n_lines + _LINES_PER_PAGE - 1) // _LINES_PER_PAGE)


def _message_lines(session, contact_id: str) -> list:
    """Flat list of (prefix|None, color|None, text_chunk), newest message first.

    Each message expands to one chunk per _MSG_CPL chars; only the first chunk
    carries the colored S↑/R↓ prefix, continuation chunks indent under the text.
    """
    lines = []
    for item in reversed(session.messages_for(contact_id)):
        if item["type"] == "sent":
            prefix, color = "S↑ ", _TEAL
        elif item.get("replay"):
            prefix, color = "R↓ ", _YELLOW
        else:
            prefix, color = "R↓ ", _GREEN
        text = item["text"]
        first = True
        while text:
            lines.append((prefix if first else None,
                          color if first else None,
                          text[:_MSG_CPL]))
            text = text[_MSG_CPL:]
            first = False
        if first:  # empty message text
            lines.append((prefix, color, ""))
    return lines


def _draw_pad_bar(hal, y: int, label: str, pct: float) -> None:
    from firmware.core.widgets.text import draw_text
    draw_text(hal, 4, y, label, font_14, _FG)
    # outlined bar
    hal.fill_rect(_BAR_X,              y,              _BAR_W, 1,      _FG)
    hal.fill_rect(_BAR_X,              y + _BAR_H - 1, _BAR_W, 1,      _FG)
    hal.fill_rect(_BAR_X,              y,              1,      _BAR_H, _FG)
    hal.fill_rect(_BAR_X + _BAR_W - 1, y,              1,      _BAR_H, _FG)
    # fill
    fill_w = int((_BAR_W - 2) * max(0.0, min(1.0, pct)))
    if fill_w > 0:
        hal.fill_rect(_BAR_X + 1, y + 1, fill_w, _BAR_H - 2, _TEAL)


class ContactThreadScreen:

    def __init__(self, hal, session, contact_id: str, name: str):
        self._hal = hal
        self._session = session
        self._contact_id = contact_id
        self._name = name

    def run(self) -> str:
        """Returns 'CONTACTS' or 'LOCK'."""
        hal = self._hal
        session = self._session
        contact_id = self._contact_id
        name = self._name
        page = [0]

        btn_back    = Button(4,  4, 60, 44, "<",            font_14, _FG, _DIM)
        btn_gear    = GearButton(280, 4, 36, 28)
        btn_new_msg = Button(40, 390, 240, 46, "New message", font_14, _BG, _TEAL)
        pagination  = {"prev": None, "next": None}

        def send_remaining():
            return PAD_SIZE - read_watermark(hal, contact_id)

        def receive_remaining():
            used = sum(e - s for s, e in read_used_ranges(hal, contact_id))
            return max(0, PAD_SIZE - used)

        def draw():
            hal.notify_screen("ContactThread")
            breadcrumb.mark(hal, "ContactThread")
            hal.fill_rect(0, 0, 320, 480, _BG)
            btn_back.draw(hal)
            btn_gear.draw(hal)

            draw_text(hal, 4, 54, name[:20], font_28, _FG)
            send_pct = send_remaining() / PAD_SIZE
            recv_pct = receive_remaining() / PAD_SIZE
            _draw_pad_bar(hal, 88,  f"Send {int(send_pct * 100)}%", send_pct)
            _draw_pad_bar(hal, 109, f"Receive {int(recv_pct * 100)}%", recv_pct)

            lines = _message_lines(session, contact_id)
            n = len(lines)
            tp = _total_pages(n)

            if n == 0:
                draw_text_centered(hal, _ITEMS_Y0 + 40, "No messages yet.", font_14, _GREY)
                pagination["prev"] = None
                pagination["next"] = None
            else:
                start = page[0] * _LINES_PER_PAGE
                for i, (prefix, color, chunk) in enumerate(lines[start: start + _LINES_PER_PAGE]):
                    y = _ITEMS_Y0 + i * _LINE_H
                    if prefix is not None:
                        draw_text(hal, 4, y, prefix, font_14, color)
                    draw_text(hal, 4 + _PREFIX_W, y, chunk, font_14, _FG)

                if tp > 1:
                    pagination["prev"] = Button(4,   350, 80, 28, "< Prev", font_14, _FG, _DIM)
                    pagination["next"] = Button(236, 350, 80, 28, "Next >", font_14, _FG, _DIM)
                    if page[0] > 0:
                        pagination["prev"].draw(hal)
                    if page[0] < tp - 1:
                        pagination["next"].draw(hal)
                else:
                    pagination["prev"] = None
                    pagination["next"] = None

            btn_new_msg.draw(hal)

        draw()

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            session.record_touch(hal)
            x, y_t = t

            if btn_back.hit_test(x, y_t):
                return "CONTACTS"

            if btn_gear.hit_test(x, y_t):
                from firmware.core.screens.contacts import ContactDetailScreen
                r = ContactDetailScreen(hal, session, contact_id, name).run()
                if r == "LOCK":
                    return "LOCK"
                if r == "CONTACTS":
                    return "CONTACTS"
                draw()  # "BACK" → stay in thread

            tp = _total_pages(len(_message_lines(session, contact_id)))

            if pagination["prev"] and page[0] > 0 and pagination["prev"].hit_test(x, y_t):
                page[0] -= 1
                draw()
            elif pagination["next"] and page[0] < tp - 1 and pagination["next"].hit_test(x, y_t):
                page[0] += 1
                draw()
            elif btn_new_msg.hit_test(x, y_t):
                from firmware.core.screens.send import SendScreen, ShowCiphertextScreen
                r = SendScreen(hal, session, contact_id).run()
                if r == "LOCK":
                    return "LOCK"
                if r is not None:
                    r2 = ShowCiphertextScreen(hal, session, r).run()
                    if r2 == "LOCK":
                        return "LOCK"
                draw()
