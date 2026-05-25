from .text import draw_text

# How long a "no touch" gap can be before we treat the next touch as a fresh
# key press. The FT6336 only reports a touch position once per IRQ - even with
# a finger held down, get_touch() returns None between IRQs. The screen loops
# poll roughly every 50 ms, so 150 ms gives ample margin to distinguish
# "finger still held but no new IRQ" from "finger actually lifted".
_HOLD_TIMEOUT_MS  = 150
_BACK_INITIAL_MS  = 400   # ms held before backspace starts repeating
_BACK_REPEAT_MS   = 80    # ms between repeats once started


def _ticks_diff(newer: int, older: int) -> int:
    return (newer - older) & 0x3FFFFFFF

# Colors (RGB565)
_KB_BG   = 0x1082  # keyboard strip background (between keys)
_KEY_BG  = 0x4A49  # regular character key
_FN_BG   = 0x2945  # function keys (shift, back, layer-switch, done)
_SH_ACT  = 0xAD75  # shift key when active
_GO_ACT  = 0x0458  # GO key when input is ready to submit
_FG      = 0xFFFF  # key label text
_SH_FG   = 0x0000  # text on active shift key
_PRESS_BG = 0x6D7F  # key press highlight (light steel blue)

# Geometry
_W     = 320
_ROW_H = 48    # row height including gap
_KEY_H = 46    # visual key height
_GAP   = 2
_KW    = 30    # standard char key width
_KU    = 32    # char key unit (key + gap)
_KBD_Y = 480 - 4 * _ROW_H  # 288 - keyboard top Y
_FN2   = 47    # shift/back key width in letter row 2
_FN3   = 77    # left/right key width in function row
_SP3   = 162   # space key width in function row
_EQ7   = 44    # equal-width key for 7-key rows

# Layer indices
_LETTERS = 0
_NUMBERS = 1
_SYMBOLS = 2

# Action sentinels (non-printable - never emitted as typed chars)
_ACT_SHIFT = '\x01'
_ACT_BACK  = '\x02'
_ACT_DONE  = '\x03'
_ACT_SPACE = '\x04'
_ACT_NUM   = '\x05'
_ACT_SYM   = '\x06'
_ACT_ABC   = '\x07'

_FN_ACTIONS = (_ACT_SHIFT, _ACT_BACK, _ACT_DONE, _ACT_NUM, _ACT_SYM, _ACT_ABC)


def _ry(r):
    return _KBD_Y + r * _ROW_H


def _row10(r, chars):
    """10 equal char keys, 1px left margin."""
    y = _ry(r)
    return [(1 + i * _KU, y, _KW, _KEY_H, c, c) for i, c in enumerate(chars)]


def _row9(r, chars):
    """9 equal char keys, centered."""
    y = _ry(r)
    sx = (_W - (9 * _KW + 8 * _GAP)) // 2
    return [(sx + i * _KU, y, _KW, _KEY_H, c, c) for i, c in enumerate(chars)]


def _row_eq7(r, items):
    """7 equal-width (44px) keys filling the full row."""
    y = _ry(r)
    unit = _EQ7 + _GAP
    return [(i * unit, y, _EQ7, _KEY_H, lbl, act) for i, (lbl, act) in enumerate(items)]


def _row_fn(r, ll, la, rl, ra):
    """Function row: [left label] [space] [right label]."""
    y = _ry(r)
    return [
        (0, y, _FN3, _KEY_H, ll, la),
        (_FN3 + _GAP, y, _SP3, _KEY_H, '', _ACT_SPACE),
        (_FN3 + _GAP + _SP3 + _GAP, y, _FN3, _KEY_H, rl, ra),
    ]


def _row_letters2():
    """Letter layer row 2: [^/SHIFT] [zxcvbnm] [</BACK]."""
    y = _ry(2)
    keys = [(0, y, _FN2, _KEY_H, '^', _ACT_SHIFT)]
    for i, c in enumerate('zxcvbnm'):
        keys.append((_FN2 + _GAP + i * _KU, y, _KW, _KEY_H, c, c))
    keys.append((_FN2 + _GAP + 7 * _KU, y, _FN2, _KEY_H, '<', _ACT_BACK))
    return keys


# Precomputed key layouts per layer - computed once at import time.
_LAYER_KEYS = [
    # 0: Letters (actions stored lowercase; shift toggles display/emit to upper)
    _row10(0, 'qwertyuiop') +
    _row9(1, 'asdfghjkl') +
    _row_letters2() +
    _row_fn(3, '123', _ACT_NUM, 'GO', _ACT_DONE),

    # 1: Numbers
    _row10(0, '1234567890') +
    _row10(1, '-/:;()$&@"') +
    _row_eq7(2, [('#+', _ACT_SYM), ('.', '.'), (',', ','), ('?', '?'),
                 ('!', '!'), ("'", "'"), ('<', _ACT_BACK)]) +
    _row_fn(3, 'ABC', _ACT_ABC, 'GO', _ACT_DONE),

    # 2: Symbols
    _row10(0, '[]{}#%^*+=') +
    _row10(1, '_\\|~<>.,;`') +
    _row_eq7(2, [('123', _ACT_NUM), ('.', '.'), (',', ','), ('?', '?'),
                 ('!', '!'), ("'", "'"), ('<', _ACT_BACK)]) +
    _row_fn(3, 'ABC', _ACT_ABC, 'GO', _ACT_DONE),
]


class Keyboard:
    """On-screen QWERTY keyboard with letters / numbers / symbols layers.

    on_char(ch)   - called with the typed character string
    on_backspace  - called when backspace is pressed
    on_done       - called when the GO/return key is pressed

    Keyboard repaints itself on layer/shift changes. Caller calls draw() once at screen entry.
    """

    def __init__(self, hal, char_font, label_font, on_char, on_backspace, on_done, initial_layer=_LETTERS):
        self._hal = hal
        self._cf = char_font    # font_28 - single-character keys
        self._lf = label_font   # font_14 - multi-char labels (123, ABC, GO, …)
        self._on_char = on_char
        self._on_back = on_backspace
        self._on_done = on_done
        self._layer = initial_layer
        self._shift = False
        self._go_active = False
        self._dirty = False
        self._held_key = None     # key tuple currently highlighted, or None
        self._last_touch_ms = 0   # hal.ticks_ms() at the last update() with a non-None touch
        self._back_press_ms = None  # ticks when backspace was first pressed this hold
        self._back_repeat_ms = 0    # ticks of last backspace repeat

    def set_go_active(self, flag: bool) -> bool:
        """Update GO key active state. Returns True if state changed."""
        if self._go_active != flag:
            self._go_active = flag
            return True
        return False

    def draw(self):
        self._held_key = None  # full repaint clears any press highlight
        hal = self._hal
        hal.fill_rect(0, _KBD_Y, _W, 4 * _ROW_H, _KB_BG)
        for key in _LAYER_KEYS[self._layer]:
            self._draw_key(key)

    def _draw_key(self, key):
        hal = self._hal
        x, y, w, h, label, action = key

        if action == _ACT_SHIFT:
            bg = _SH_ACT if self._shift else _FN_BG
            fg = _SH_FG if self._shift else _FG
        elif action == _ACT_DONE:
            bg = _GO_ACT if self._go_active else _FN_BG
            fg = _FG
        elif action in _FN_ACTIONS or action == _ACT_SPACE:
            bg = _FN_BG if action != _ACT_SPACE else _KEY_BG
            fg = _FG
        else:
            bg = _KEY_BG
            fg = _FG

        hal.fill_rect(x, y, w, h, bg)

        disp = label
        if self._layer == _LETTERS and len(label) == 1 and label.isalpha():
            disp = label.upper() if self._shift else label

        if not disp:
            return

        font = self._cf if len(disp) == 1 else self._lf
        tx = x + (w - len(disp) * font.max_width()) // 2
        ty = y + (h - font.height()) // 2
        draw_text(hal, tx, ty, disp, font, fg, bg)

    def _draw_key_highlighted(self, key):
        hal = self._hal
        x, y, w, h, label, action = key
        hal.fill_rect(x, y, w, h, _PRESS_BG)
        disp = label
        if self._layer == _LETTERS and len(label) == 1 and label.isalpha():
            disp = label.upper() if self._shift else label
        if not disp:
            return
        font = self._cf if len(disp) == 1 else self._lf
        tx = x + (w - len(disp) * font.max_width()) // 2
        ty = y + (h - font.height()) // 2
        draw_text(hal, tx, ty, disp, font, _FG, _PRESS_BG)

    def update(self, touch):
        """Drive the keyboard. Call once per screen-loop iteration.

        touch: (x, y) when the finger is down, None otherwise.

        Character keys fire exactly once per press. Backspace fires on press
        then repeats after _BACK_INITIAL_MS at _BACK_REPEAT_MS intervals.
        Sliding to a different key while held fires the new key (iOS behavior).
        The finger is considered "lifted" once update(None) has been called
        for _HOLD_TIMEOUT_MS.

        Returns True if a touch was handled by a key (so the host screen knows
        the touch wasn't a miss). Returns False for the no-touch case and for
        touches outside the keyboard area.
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
        for key in _LAYER_KEYS[self._layer]:
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
                    if self._dirty:
                        self.draw()
                        self._dirty = False
                        # Finger still held after layer repaint - re-establish
                        # _held_key in the new layer so the repeat guard fires
                        # correctly and doesn't retrigger a new-layer key.
                        for nk in _LAYER_KEYS[self._layer]:
                            if nk[0] <= x < nk[0] + nk[2] and nk[1] <= y < nk[1] + nk[3]:
                                self._held_key = nk
                                break
                return True
        if self._held_key is not None:
            self._draw_key(self._held_key)
            self._held_key = None
            self._back_press_ms = None
        return False

    def _act(self, action, label):
        if action == _ACT_SHIFT:
            self._shift = not self._shift
            self._dirty = True
        elif action == _ACT_BACK:
            self._on_back()
        elif action == _ACT_DONE:
            self._on_done()
        elif action == _ACT_SPACE:
            self._emit(' ')
        elif action == _ACT_NUM:
            self._layer = _NUMBERS
            self._shift = False
            self._dirty = True
        elif action == _ACT_SYM:
            self._layer = _SYMBOLS
            self._shift = False
            self._dirty = True
        elif action == _ACT_ABC:
            self._layer = _LETTERS
            self._shift = False
            self._dirty = True
        else:
            ch = label.upper() if (self._layer == _LETTERS and self._shift) else label
            self._emit(ch)

    def _emit(self, ch):
        if self._layer == _LETTERS and self._shift:
            self._shift = False
            self._dirty = True
        self._on_char(ch)
