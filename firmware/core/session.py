_AUTOLOCK_MS = 5 * 60 * 1000  # 5 minutes


def _ticks_diff(newer: int, older: int) -> int:
    return (newer - older) & 0x3FFFFFFF


class Session:
    """In-RAM state that lives for one authenticated session (PIN unlock → auto-lock)."""

    def __init__(self, hal):
        self.message_history = []
        self._last_touch_ms = hal.ticks_ms()

    def record_touch(self, hal) -> None:
        self._last_touch_ms = hal.ticks_ms()

    def is_idle_expired(self, hal) -> bool:
        return _ticks_diff(hal.ticks_ms(), self._last_touch_ms) >= _AUTOLOCK_MS

    def messages_for(self, contact_id: str) -> list:
        return [m for m in self.message_history if m.get("contact_id") == contact_id]

    def lock(self, hal) -> None:
        hal.lock_secrets()
        self.message_history.clear()
