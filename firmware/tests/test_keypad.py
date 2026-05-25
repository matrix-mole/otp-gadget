from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.keypad import (
    Keypad, DIGIT_LAYOUT, HEX_LAYOUT, _KBD_Y, _ACT_BACK, _ACT_DONE,
    _BACK_INITIAL_MS, _BACK_REPEAT_MS,
)


class FakeHAL:
    def __init__(self):
        self._t = 0
    def fill_rect(self, *a): pass
    def blit_rect(self, *a): pass
    def ticks_ms(self):
        # Advance enough on every call that consecutive _hit()s count as
        # fresh presses (matches lift-finger-between-taps semantics).
        self._t += 1000
        return self._t


class ControlledHAL:
    def __init__(self):
        self._t = 0
    def fill_rect(self, *a): pass
    def blit_rect(self, *a): pass
    def ticks_ms(self):
        return self._t


def _make(layout):
    chars, backs, dones = [], [], []
    kb = Keypad(
        FakeHAL(), font_28, font_14,
        on_char=lambda ch: chars.append(ch),
        on_backspace=lambda: backs.append(1),
        on_done=lambda: dones.append(1),
        layout=layout,
    )
    return kb, chars, backs, dones


def _center(key):
    x, y, w, h = key[0], key[1], key[2], key[3]
    return x + w // 2, y + h // 2


class TestDigitKeypad:
    def test_emits_each_digit(self):
        for digit in '1234567890':
            kb, chars, backs, dones = _make(DIGIT_LAYOUT)
            key = next(k for k in DIGIT_LAYOUT if k[4] == digit)
            assert kb.update(_center(key)) is True
            assert chars == [digit]

    def test_backspace(self):
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        key = next(k for k in DIGIT_LAYOUT if k[5] == _ACT_BACK)
        assert kb.update(_center(key)) is True
        assert backs == [1]
        assert chars == []

    def test_done(self):
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        key = next(k for k in DIGIT_LAYOUT if k[5] == _ACT_DONE)
        assert kb.update(_center(key)) is True
        assert dones == [1]

    def test_only_digit_and_fn_actions(self):
        for key in DIGIT_LAYOUT:
            action = key[5]
            assert action in (_ACT_BACK, _ACT_DONE) or action in '0123456789'

    def test_miss_above_keypad(self):
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        assert kb.update((100, _KBD_Y - 1)) is False
        assert chars == []

    def test_miss_left_of_keypad(self):
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        assert kb.update((-1, _KBD_Y + 24)) is False

    def test_all_keys_within_screen(self):
        for key in DIGIT_LAYOUT:
            x, y, w, h = key[0], key[1], key[2], key[3]
            assert x >= 0 and y >= _KBD_Y
            assert x + w <= 320 and y + h <= 480

    def test_draw_does_not_raise(self):
        kb, *_ = _make(DIGIT_LAYOUT)
        kb.draw()  # must not raise


class TestHexKeypad:
    def test_emits_each_hex_char(self):
        for char in '0123456789ABCDEF':
            kb, chars, backs, dones = _make(HEX_LAYOUT)
            key = next(k for k in HEX_LAYOUT if k[4] == char)
            assert kb.update(_center(key)) is True
            assert chars == [char], f"expected {char!r}, got {chars}"

    def test_uppercase_only(self):
        for key in HEX_LAYOUT:
            label, action = key[4], key[5]
            if action not in (_ACT_BACK, _ACT_DONE) and label:
                assert label == label.upper()

    def test_only_hex_and_fn_actions(self):
        valid = set('0123456789ABCDEF')
        for key in HEX_LAYOUT:
            action = key[5]
            assert action in (_ACT_BACK, _ACT_DONE) or action in valid

    def test_miss_above_keypad(self):
        kb, chars, backs, dones = _make(HEX_LAYOUT)
        assert kb.update((160, _KBD_Y - 1)) is False

    def test_all_keys_within_screen(self):
        for key in HEX_LAYOUT:
            x, y, w, h = key[0], key[1], key[2], key[3]
            assert x >= 0 and y >= _KBD_Y
            assert x + w <= 320 and y + h <= 480

    def test_draw_does_not_raise(self):
        kb, *_ = _make(HEX_LAYOUT)
        kb.draw()


class TestLayoutCounts:
    def test_digit_key_count(self):
        assert len(DIGIT_LAYOUT) == 12  # 9 digits + 0 + back + GO

    def test_hex_key_count(self):
        assert len(HEX_LAYOUT) == 18  # 16 hex chars + back + GO


class TestGapsAndEdges:
    def test_digit_inter_key_gap(self):
        # x=106 is the 2 px gap between col 0 (x=2..106) and col 1 (x=108..212) on row 0
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        assert kb.update((106, _KBD_Y + 24)) is False

    def test_digit_right_edge_dead_zone(self):
        # x=318..319 is outside all digit keys (last col ends at x=318)
        kb, chars, backs, dones = _make(DIGIT_LAYOUT)
        assert kb.update((319, _KBD_Y + 24)) is False

    def test_hex_inter_key_gap(self):
        # x=78..79 is the 2 px gap between col 0 (x=0..78) and col 1 (x=80..158)
        kb, chars, backs, dones = _make(HEX_LAYOUT)
        assert kb.update((78, _KBD_Y + 24)) is False

    def test_hex_right_edge_in_last_column(self):
        # last col x=240..317 (half-open); x=317 is inside → 'F'; x=318 is dead zone
        kb, chars, backs, dones = _make(HEX_LAYOUT)
        assert kb.update((317, _KBD_Y + 3 * 48 + 24)) is True  # row 3, col 3 = 'F'
        assert chars == ['F']

    def test_hex_right_dead_zone(self):
        # x=318..319 is outside all hex keys (last col ends at x=317)
        kb, chars, backs, dones = _make(HEX_LAYOUT)
        assert kb.update((318, _KBD_Y + 3 * 48 + 24)) is False


class TestBackspaceHoldRepeat:
    def _setup(self, layout):
        chars, backs, dones = [], [], []
        hal = ControlledHAL()
        kb = Keypad(
            hal, font_28, font_14,
            on_char=lambda ch: chars.append(ch),
            on_backspace=lambda: backs.append(1),
            on_done=lambda: dones.append(1),
            layout=layout,
        )
        back_key = next(k for k in layout if k[5] == _ACT_BACK)
        cx, cy = back_key[0] + back_key[2] // 2, back_key[1] + back_key[3] // 2
        return kb, hal, chars, backs, cx, cy

    def _press_and_hold(self, kb, hal, cx, cy):
        hal._t = 0
        kb.update((cx, cy))
        for t in (100, 200, 300):
            hal._t = t
            kb.update((cx, cy))

    def test_no_repeat_before_initial_delay(self):
        kb, hal, chars, backs, cx, cy = self._setup(DIGIT_LAYOUT)
        self._press_and_hold(kb, hal, cx, cy)
        hal._t = 350
        kb.update(None)
        assert backs == [1]

    def test_repeats_after_initial_delay(self):
        kb, hal, chars, backs, cx, cy = self._setup(DIGIT_LAYOUT)
        self._press_and_hold(kb, hal, cx, cy)
        hal._t = _BACK_INITIAL_MS + 10
        kb.update(None)
        assert len(backs) >= 2

    def test_hex_repeats_after_initial_delay(self):
        kb, hal, chars, backs, cx, cy = self._setup(HEX_LAYOUT)
        self._press_and_hold(kb, hal, cx, cy)
        hal._t = _BACK_INITIAL_MS + 10
        kb.update(None)
        assert len(backs) >= 2


class TestLayoutNoOverlap:
    def _overlaps(self, a, b):
        ax, ay, aw, ah = a[0], a[1], a[2], a[3]
        bx, by, bw, bh = b[0], b[1], b[2], b[3]
        return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)

    def test_digit_no_overlap(self):
        for i, a in enumerate(DIGIT_LAYOUT):
            for b in DIGIT_LAYOUT[i + 1:]:
                assert not self._overlaps(a, b), f"overlap: {a} vs {b}"

    def test_hex_no_overlap(self):
        for i, a in enumerate(HEX_LAYOUT):
            for b in HEX_LAYOUT[i + 1:]:
                assert not self._overlaps(a, b), f"overlap: {a} vs {b}"
