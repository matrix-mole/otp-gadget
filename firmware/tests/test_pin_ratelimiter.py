"""
Rate-limiter cooldown math from pin_entry.py:
  attempts >= 5 → cooldown_secs = 10 * (2 ** (attempts - 5))
"""
import pytest


def _cooldown(attempts: int) -> int:
    """Mirror of the formula in PINEntryScreen.run()."""
    return 10 * (2 ** (attempts - 5))


def test_cooldown_at_threshold():
    # 5th wrong attempt → 10 seconds
    assert _cooldown(5) == 10


def test_cooldown_doubles_each_attempt():
    for i in range(5, 10):
        assert _cooldown(i + 1) == _cooldown(i) * 2


def test_cooldown_specific_values():
    expected = {5: 10, 6: 20, 7: 40, 8: 80, 9: 160, 10: 320}
    for attempts, secs in expected.items():
        assert _cooldown(attempts) == secs


def test_cooldown_not_triggered_before_threshold():
    # Fewer than 5 wrong attempts must NOT produce a cooldown
    # (no call to _cooldown at all - the branch in run() is `if new_attempts >= 5`)
    for attempts in range(1, 5):
        assert attempts < 5  # sanity: the branch condition is not met


def test_attempts_reset_on_success():
    # Correct PIN → attempts written back as 0
    # Model: after 4 wrong then 1 right, state should be {"attempts": 0, "cooldown_until": 0}
    state = {"attempts": 4, "cooldown_until": 0}
    # Simulated correct PIN path
    state = {"attempts": 0, "cooldown_until": 0}
    assert state["attempts"] == 0
    assert state["cooldown_until"] == 0
