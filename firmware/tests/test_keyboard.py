from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.keyboard import (
    Keyboard, _LAYER_KEYS, _LETTERS, _NUMBERS, _ACT_DONE, _ACT_SHIFT,
    _BACK_INITIAL_MS, _BACK_REPEAT_MS,
)


class FakeHAL:
    def __init__(self):
        self._t = 0
    def fill_rect(self, *a): pass
    def blit_rect(self, *a): pass
    def ticks_ms(self):
        # Advance by more than _HOLD_TIMEOUT_MS on every call so each
        # _hit() in the tests counts as a fresh press (matches the
        # "lift finger between taps" semantics the tests assume).
        self._t += 1000
        return self._t


class ControlledHAL:
    """HAL with manually controlled time for hold/repeat tests."""
    def __init__(self):
        self._t = 0
    def fill_rect(self, *a): pass
    def blit_rect(self, *a): pass
    def ticks_ms(self):
        return self._t


def _make_controlled():
    chars, backs, dones = [], [], []
    hal = ControlledHAL()
    kb = Keyboard(
        hal, font_28, font_14,
        on_char=lambda ch: chars.append(ch),
        on_backspace=lambda: backs.append(1),
        on_done=lambda: dones.append(1),
    )
    return kb, hal, chars, backs, dones


def _back_key_center(kb):
    for key in _LAYER_KEYS[kb._layer]:
        if key[4] == '<':
            return key[0] + key[2] // 2, key[1] + key[3] // 2
    raise RuntimeError('no backspace key')


def _make(initial_layer=_LETTERS):
    chars, backs, dones = [], [], []
    kb = Keyboard(
        FakeHAL(), font_28, font_14,
        on_char=lambda ch: chars.append(ch),
        on_backspace=lambda: backs.append(1),
        on_done=lambda: dones.append(1),
        initial_layer=initial_layer,
    )
    return kb, chars, backs, dones


def _hit(kb, label):
    """Tap the key with the given label in the current layer."""
    for key in _LAYER_KEYS[kb._layer]:
        if key[4] == label:
            cx = key[0] + key[2] // 2
            cy = key[1] + key[3] // 2
            return kb.update((cx, cy))
    return False


class TestKeyboardBasic:
    def test_emit_char(self):
        kb, chars, backs, dones = _make()
        _hit(kb, 'a')
        assert chars == ['a']

    def test_backspace(self):
        kb, chars, backs, dones = _make()
        _hit(kb, '<')
        assert backs == [1]

    def test_done(self):
        kb, chars, backs, dones = _make()
        _hit(kb, 'GO')
        assert dones == [1]

    def test_shift_uppercases_one_char(self):
        kb, chars, backs, dones = _make()
        _hit(kb, '^')   # shift on
        _hit(kb, 'a')   # emits 'A'
        assert chars == ['A']

    def test_shift_auto_resets_after_one_char(self):
        kb, chars, backs, dones = _make()
        _hit(kb, '^')
        _hit(kb, 'a')   # 'A' - shift resets
        _hit(kb, 'b')   # 'b' - no shift
        assert chars == ['A', 'b']

    def test_layer_switch_to_numbers(self):
        kb, chars, backs, dones = _make()
        _hit(kb, '123')
        assert kb._layer == _NUMBERS

    def test_layer_switch_back_to_letters(self):
        kb, chars, backs, dones = _make(initial_layer=_NUMBERS)
        _hit(kb, 'ABC')
        assert kb._layer == _LETTERS

    def test_miss_above_keyboard(self):
        from firmware.core.widgets.keyboard import _KBD_Y
        kb, chars, backs, dones = _make()
        assert kb.update((160, _KBD_Y - 1)) is False
        assert chars == []

    def test_draw_does_not_raise(self):
        kb, *_ = _make()
        kb.draw()


class TestBackspaceHoldRepeat:
    def _press_and_hold(self, kb, hal, cx, cy):
        """Simulate a finger press then periodic IRQ refreshes so the widget
        considers the finger still held."""
        hal._t = 0
        kb.update((cx, cy))          # initial press
        # IRQ refreshes at 100ms, 200ms, 300ms keep _last_touch_ms current
        for t in (100, 200, 300):
            hal._t = t
            kb.update((cx, cy))

    def test_no_repeat_before_initial_delay(self):
        kb, hal, chars, backs, dones = _make_controlled()
        cx, cy = _back_key_center(kb)
        self._press_and_hold(kb, hal, cx, cy)

        # Check at 350ms: well under _BACK_INITIAL_MS (400ms)
        hal._t = 350
        kb.update(None)

        assert backs == [1]  # only the initial press, no repeat

    def test_repeats_after_initial_delay(self):
        kb, hal, chars, backs, dones = _make_controlled()
        cx, cy = _back_key_center(kb)
        self._press_and_hold(kb, hal, cx, cy)

        # Cross the 400ms initial delay threshold
        hal._t = _BACK_INITIAL_MS + 10
        kb.update(None)

        assert len(backs) >= 2  # initial press + at least one repeat

    def test_repeats_at_repeat_interval(self):
        kb, hal, chars, backs, dones = _make_controlled()
        cx, cy = _back_key_center(kb)
        self._press_and_hold(kb, hal, cx, cy)

        # First repeat
        hal._t = _BACK_INITIAL_MS + 10
        kb.update(None)
        count_after_first = len(backs)

        # IRQ refresh so finger stays held
        hal._t = _BACK_INITIAL_MS + 20
        kb.update((cx, cy))

        # Second repeat: advance by more than _BACK_REPEAT_MS
        hal._t = _BACK_INITIAL_MS + 10 + _BACK_REPEAT_MS + 10
        kb.update(None)

        assert len(backs) > count_after_first  # another repeat fired

    def test_char_keys_still_no_repeat(self):
        kb, hal, chars, backs, dones = _make_controlled()
        key_a = next(k for k in _LAYER_KEYS[_LETTERS] if k[4] == 'a')
        cx, cy = key_a[0] + key_a[2] // 2, key_a[1] + key_a[3] // 2

        hal._t = 0
        kb.update((cx, cy))
        # IRQ refreshes every 50ms keep the finger "held" (well under _HOLD_TIMEOUT_MS)
        for t in range(50, 1001, 50):
            hal._t = t
            kb.update((cx, cy))

        assert chars == ['a']  # no repeat regardless of hold duration
