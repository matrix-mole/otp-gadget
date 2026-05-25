"""Tests for Session in-RAM store (session.py).

Covers:
- messages_for filters by contact_id
- messages_for returns empty for unknown contact_id (covers deleted-contact scenario)
- messages_for returns empty when history is empty
- lock() clears message history
"""
from firmware.core.session import Session


class _FakeHal:
    def __init__(self):
        self.lock_secrets_called = False

    def ticks_ms(self):
        return 0

    def lock_secrets(self):
        self.lock_secrets_called = True


def test_messages_for_filters_by_contact():
    hal = _FakeHal()
    s = Session(hal)
    s.message_history = [
        {"type": "sent",     "text": "hi",    "contact_id": "aaaaaaaa"},
        {"type": "received", "text": "hello", "contact_id": "bbbbbbbb"},
        {"type": "sent",     "text": "bye",   "contact_id": "aaaaaaaa"},
    ]
    msgs = s.messages_for("aaaaaaaa")
    assert len(msgs) == 2
    assert all(m["contact_id"] == "aaaaaaaa" for m in msgs)


def test_messages_for_returns_empty_for_unknown_contact():
    """Unknown contact_id (e.g. after delete) returns empty - no bleed-through."""
    hal = _FakeHal()
    s = Session(hal)
    s.message_history = [
        {"type": "sent", "text": "hi", "contact_id": "aaaaaaaa"},
    ]
    assert s.messages_for("nonexistent") == []


def test_messages_for_empty_history():
    hal = _FakeHal()
    s = Session(hal)
    assert s.messages_for("aaaaaaaa") == []


def test_lock_clears_message_history():
    hal = _FakeHal()
    s = Session(hal)
    s.message_history = [
        {"type": "sent", "text": "secret", "contact_id": "aaaaaaaa"},
    ]
    s.lock(hal)
    assert s.message_history == []
    assert hal.lock_secrets_called
