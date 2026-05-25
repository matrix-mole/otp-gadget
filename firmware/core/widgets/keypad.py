from .text import draw_text

# See keyboard.py for rationale - debounce against FT6336 IRQ gaps so a
# held finger does not register as repeated key presses.
_HOLD_TIMEOUT_MS  = 150
_BACK_INITIAL_MS  = 400
_BACK_REPEAT_MS   = 80


def _ticks_diff(newer: int, older: int) -> int:
    return (newer - older) & 0x3FFFFFFF

# Match keyboard.py colors - keep in sync.
_KB_BG    = 0x1082  # keyboard strip background
_KEY_BG   = 0x4A49  # regular character key
_FN_BG    = 0x2945  # function keys (back)
_GO_ACT   = 0x0458  # GO key (always active)
_FG       = 0xFFFF  # key label text
_PRESS_BG = 0x6D7F  # key press highlight (light steel blue)

_W      = 320
_ROW_H  = 48
_KEY_H  = 46
_GAP    = 2
_KBD_Y  = 240  # 480 - 5 * _ROW_H

_ACT_BACK = '\x02'
_ACT_DONE = '\x03'


# ── Digit layout ──────────────────────────────────────────────────────────────
# 3 cols (key_w=104, gap=2, left_margin=2 → x positions: 2, 108, 214)
# Rows 0-2: digits 1-9.  Row 3: < 0 GO.
# Starts 1 row lower than hex (_KBD_Y + _ROW_H) since it only needs 4 rows.
_DK_W = 104
_DK_X = (2, 108, 214)
_DK_Y = _KBD_Y + _ROW_H


def _dk_row(r, chars):
    y = _DK_Y + r * _ROW_H
    return [(x, y, _DK_W, _KEY_H, c, c) for x, c in zip(_DK_X, chars)]


DIGIT_LAYOUT = (
    _dk_row(0, '123') +
    _dk_row(1, '456') +
    _dk_row(2, '789') +
    [
        (_DK_X[0], _DK_Y + 3 * _ROW_H, _DK_W, _KEY_H, '<',  _ACT_BACK),
        (_DK_X[1], _DK_Y + 3 * _ROW_H, _DK_W, _KEY_H, '0',  '0'),
        (_DK_X[2], _DK_Y + 3 * _ROW_H, _DK_W, _KEY_H, 'GO', _ACT_DONE),
    ]
)


# ── Hex layout ────────────────────────────────────────────────────────────────
# 4 cols (key_w=78, gap=2, left_margin=0 → x positions: 0, 80, 160, 240)
# Rows 0-3: hex chars 0-F in reading order.  Row 4: function row.
_HK_W = 78
_HK_X = (0, 80, 160, 240)


def _hk_row(r, chars):
    y = _KBD_Y + r * _ROW_H
    return [(x, y, _HK_W, _KEY_H, c, c) for x, c in zip(_HK_X, chars)]


HEX_LAYOUT = (
    _hk_row(0, '0123') +
    _hk_row(1, '4567') +
    _hk_row(2, '89AB') +
    _hk_row(3, 'CDEF') +
    [
        (0,   _KBD_Y + 4 * _ROW_H, 158, _KEY_H, '<',  _ACT_BACK),
        (160, _KBD_Y + 4 * _ROW_H, 160, _KEY_H, 'GO', _ACT_DONE),
    ]
)


class Keypad:
    """Fixed-key grid keypad driven by a layout config.

    on_char(ch)  - called with the typed character string
    on_backspace - called when < key is pressed
    on_done      - called when GO key is pressed

    Caller calls draw() once at screen entry; no internal repaint needed
    (no shift, no layer switching, no key-state changes).
    """

    def __init__(self, hal, char_font, label_font, on_char, on_backspace, on_done, layout):
        self._hal = hal
        self._cf = char_font    # font_28 - single-char keys
        self._lf = label_font   # font_14 - multi-char labels (GO, <)
        self._on_char = on_char
        self._on_back = on_backspace
        self._on_done = on_done
        self._layout = layout
        self._held_key = None     # key tuple currently highlighted, or None
        self._last_touch_ms = 0   # hal.ticks_ms() at the last update() with a non-None touch
        self._back_press_ms = None
        self._back_repeat_ms = 0

    def draw(self):
        self._held_key = None  # full repaint clears any press highlight
        self._hal.fill_rect(0, _KBD_Y, _W, 480 - _KBD_Y, _KB_BG)
        for key in self._layout:
            self._draw_key(key)

    def _draw_key(self, key):
        hal = self._hal
        x, y, w, h, label, action = key
        if action == _ACT_BACK:
            bg = _FN_BG
        elif action == _ACT_DONE:
            bg = _GO_ACT
        else:
            bg = _KEY_BG
        hal.fill_rect(x, y, w, h, bg)
        if not label:
            return
        font = self._cf if len(label) == 1 else self._lf
        tx = x + (w - len(label) * font.max_width()) // 2
        ty = y + (h - font.height()) // 2
        draw_text(hal, tx, ty, label, font, _FG, bg)

    def _draw_key_highlighted(self, key):
        hal = self._hal
        x, y, w, h, label, action = key
        hal.fill_rect(x, y, w, h, _PRESS_BG)
        if not label:
            return
        font = self._cf if len(label) == 1 else self._lf
        tx = x + (w - len(label) * font.max_width()) // 2
        ty = y + (h - font.height()) // 2
        draw_text(hal, tx, ty, label, font, _FG, _PRESS_BG)

    def update(self, touch) -> bool:
        """Drive the keypad. Call once per screen-loop iteration.

        touch: (x, y) when the finger is down, None otherwise.
        See keyboard.py.update for full semantics.
        """
        if touch is None:
            if self._held_key is not None:
                now = self._hal.ticks_ms()
                if _ticks_diff(now, self._last_touch_ms) > _HOLD_TIMEOUT_MS:
                    self._draw_key(self._held_key)
                    self._held_key = None
                    self._back_press_ms = None
                elif self._back_press_ms is not None:
                    held_for = _ticks_diff(now, self._back_press_ms)
                    if held_for > _BACK_INITIAL_MS and _ticks_diff(now, self._back_repeat_ms) > _BACK_REPEAT_MS:
                        self._on_back()
                        self._back_repeat_ms = now
            return False

        x, y = touch
        now = self._hal.ticks_ms()
        if _ticks_diff(now, self._last_touch_ms) > _HOLD_TIMEOUT_MS:
            if self._held_key is not None:
                self._draw_key(self._held_key)
                self._held_key = None
                self._back_press_ms = None
        self._last_touch_ms = now
        for key in self._layout:
            kx, ky, kw, kh, label, action = key
            if kx <= x < kx + kw and ky <= y < ky + kh:
                if key != self._held_key:
                    if self._held_key is not None:
                        self._draw_key(self._held_key)
                    self._held_key = key
                    self._draw_key_highlighted(key)
                    if action == _ACT_BACK:
                        self._back_press_ms = now
                        self._back_repeat_ms = now
                    else:
                        self._back_press_ms = None
                    self._act(action, label)
                return True
        if self._held_key is not None:
            self._draw_key(self._held_key)
            self._held_key = None
            self._back_press_ms = None
        return False

    def _act(self, action, label):
        if action == _ACT_BACK:
            self._on_back()
        elif action == _ACT_DONE:
            self._on_done()
        else:
            self._on_char(label)
