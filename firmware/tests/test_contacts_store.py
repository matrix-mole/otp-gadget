import json
import pytest

from firmware.core.contacts_store import (
    list_contacts,
    get_in_flight,
    find_by_name_ci,
    paths_for,
    pads_valid,
    commit_contact,
    delete_contact,
    set_in_flight,
    reconcile_in_flight,
)

_MANIFEST_PATH = "/secret/contacts.json"


class _FakeHal:
    def __init__(self):
        self._secrets = {}
        self._files = {}  # (slot, path) -> True

    def read_secret(self, path):
        if path not in self._secrets:
            raise OSError(f"not found: {path}")
        return self._secrets[path]

    def write_secret(self, path, data):
        self._secrets[path] = data

    def file_exists(self, slot, path):
        return (slot, path) in self._files

    def _put_file(self, slot, path):
        self._files[(slot, path)] = True

    def _put_manifest(self, manifest: dict):
        self._secrets[_MANIFEST_PATH] = json.dumps(manifest).encode("utf-8")


# ── list_contacts ──────────────────────────────────────────────────────────────

def test_list_contacts_empty_when_no_manifest():
    assert list_contacts(_FakeHal()) == []


def test_list_contacts_empty_manifest():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    assert list_contacts(hal) == []


def test_list_contacts_returns_sorted_by_created_at():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "bbbbbbbb", "name": "Bob",   "created_at": 2000},
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 1000},
    ]})
    result = list_contacts(hal)
    assert [c["name"] for c in result] == ["Alice", "Bob"]


def test_list_contacts_tiebreaker_by_id():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "zzzzzzzz", "name": "Z", "created_at": 1000},
        {"id": "aaaaaaaa", "name": "A", "created_at": 1000},
    ]})
    result = list_contacts(hal)
    assert result[0]["id"] == "aaaaaaaa"
    assert result[1]["id"] == "zzzzzzzz"


# ── commit_contact ─────────────────────────────────────────────────────────────

def test_commit_contact_appends_and_clears_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "a1b2c3d4", "name": "Alice", "started_at": 1000, "kind": "add"}, "contacts": []})
    commit_contact(hal, "a1b2c3d4", "Alice", 1000)
    contacts = list_contacts(hal)
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Alice"
    assert get_in_flight(hal) is None


def test_commit_contact_preserves_existing():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Existing", "created_at": 500},
    ]})
    commit_contact(hal, "bbbbbbbb", "New", 1000)
    assert len(list_contacts(hal)) == 2


# ── delete_contact ─────────────────────────────────────────────────────────────

def test_delete_contact_removes_by_id():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 1000},
        {"id": "bbbbbbbb", "name": "Bob",   "created_at": 2000},
    ]})
    delete_contact(hal, "aaaaaaaa")
    contacts = list_contacts(hal)
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Bob"


def test_delete_contact_noop_on_missing_id():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 1000},
    ]})
    delete_contact(hal, "xxxxxxxx")  # does not exist
    assert len(list_contacts(hal)) == 1


# ── set_in_flight / get_in_flight ─────────────────────────────────────────────

def test_set_and_get_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    inf = {"id": "a1b2c3d4", "name": "Alice", "started_at": 9000, "kind": "add"}
    set_in_flight(hal, inf)
    assert get_in_flight(hal) == inf


def test_clear_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "x", "name": "X", "started_at": 1, "kind": "add"}, "contacts": []})
    set_in_flight(hal, None)
    assert get_in_flight(hal) is None


# ── find_by_name_ci ────────────────────────────────────────────────────────────

def test_find_by_name_ci_case_insensitive():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 1000},
    ]})
    assert find_by_name_ci(hal, "alice") == "aaaaaaaa"
    assert find_by_name_ci(hal, "ALICE") == "aaaaaaaa"
    assert find_by_name_ci(hal, "  Alice  ") == "aaaaaaaa"


def test_find_by_name_ci_no_match():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 1000},
    ]})
    assert find_by_name_ci(hal, "Bob") is None


def test_find_by_name_ci_checks_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "zzzzzzzz", "name": "Zara", "started_at": 1, "kind": "add"}, "contacts": []})
    assert find_by_name_ci(hal, "zara") == "zzzzzzzz"


def test_find_by_name_ci_empty():
    assert find_by_name_ci(_FakeHal(), "Alice") is None


# ── paths_for ──────────────────────────────────────────────────────────────────

def test_paths_for_returns_correct_paths():
    p = paths_for("a1b2c3d4")
    assert p["pad_send"]                == "/secret/contacts/a1b2c3d4/pad_send.bin"
    assert p["pad_receive"]             == "/secret/contacts/a1b2c3d4/pad_receive.bin"
    assert p["pad_send_watermark"]      == "/secret/contacts/a1b2c3d4/pad_send_watermark.txt"
    assert p["pad_receive_used_ranges"] == "/secret/contacts/a1b2c3d4/pad_receive_used_ranges.json"


# ── pads_valid ─────────────────────────────────────────────────────────────────

def test_pads_valid_both_present():
    hal = _FakeHal()
    hal._put_file("own", "/secret/contacts/a1b2c3d4/pad_send.bin")
    hal._put_file("own", "/secret/contacts/a1b2c3d4/pad_receive.bin")
    assert pads_valid(hal, "a1b2c3d4")


def test_pads_valid_send_missing():
    hal = _FakeHal()
    hal._put_file("own", "/secret/contacts/a1b2c3d4/pad_receive.bin")
    assert not pads_valid(hal, "a1b2c3d4")


def test_pads_valid_receive_missing():
    hal = _FakeHal()
    hal._put_file("own", "/secret/contacts/a1b2c3d4/pad_send.bin")
    assert not pads_valid(hal, "a1b2c3d4")


def test_pads_valid_neither_present():
    assert not pads_valid(_FakeHal(), "a1b2c3d4")


# ── reconcile_in_flight ────────────────────────────────────────────────────────

def test_reconcile_noop_when_no_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is None
    assert list_contacts(hal) == []


def test_reconcile_noop_when_staging_present():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "A", "started_at": 1, "kind": "add"}, "contacts": []})
    hal._put_file("own", "/exchange/X_own.bin")
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is not None  # untouched


def test_reconcile_noop_when_otp_staging_present():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "A", "started_at": 1, "kind": "add"}, "contacts": []})
    hal._put_file("own", "/exchange/OTP.bin")
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is not None


def test_reconcile_add_valid_pads_commits_silently():
    """Power-loss after pad write but before manifest commit → commits the contact."""
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "Alice", "started_at": 1000, "kind": "add"}, "contacts": []})
    hal._put_file("own", "/secret/contacts/aaaaaaaa/pad_send.bin")
    hal._put_file("own", "/secret/contacts/aaaaaaaa/pad_receive.bin")
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is None
    contacts = list_contacts(hal)
    assert len(contacts) == 1
    assert contacts[0]["id"] == "aaaaaaaa"
    assert contacts[0]["name"] == "Alice"
    assert contacts[0]["created_at"] == 1000


def test_reconcile_add_no_pads_clears_silently():
    """User cancelled before any work was committed."""
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "Alice", "started_at": 1000, "kind": "add"}, "contacts": []})
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is None
    assert list_contacts(hal) == []


def test_reconcile_reexchange_valid_pads_clears_in_flight():
    """Power-loss during reexchange after pads written → contact stays, in_flight cleared."""
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "Alice", "started_at": 1000, "kind": "reexchange"}, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 900},
    ]})
    hal._put_file("own", "/secret/contacts/aaaaaaaa/pad_send.bin")
    hal._put_file("own", "/secret/contacts/aaaaaaaa/pad_receive.bin")
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is None
    contacts = list_contacts(hal)
    assert len(contacts) == 1  # contact still present


def test_reconcile_reexchange_invalid_pads_clears_in_flight():
    """Interrupted reexchange (pads wiped, new ones never written) → contact stays as interrupted."""
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "aaaaaaaa", "name": "Alice", "started_at": 1000, "kind": "reexchange"}, "contacts": [
        {"id": "aaaaaaaa", "name": "Alice", "created_at": 900},
    ]})
    reconcile_in_flight(hal)
    assert get_in_flight(hal) is None
    assert len(list_contacts(hal)) == 1  # contact still present, will show Re-exchange interrupted badge


# ── name-uniqueness integration ────────────────────────────────────────────────

def test_name_uniqueness_blocks_duplicate_committed():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": [
        {"id": "aaaaaaaa", "name": "Martin", "created_at": 1000},
    ]})
    assert find_by_name_ci(hal, "martin") is not None  # collision detected


def test_name_uniqueness_blocks_duplicate_in_flight():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "zzzzzzzz", "name": "Martin", "started_at": 1, "kind": "add"}, "contacts": []})
    assert find_by_name_ci(hal, "martin") is not None


def test_name_uniqueness_allows_distinct_names():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": {"id": "zzzzzzzz", "name": "Martin S", "started_at": 1, "kind": "add"}, "contacts": [
        {"id": "aaaaaaaa", "name": "Martin W", "created_at": 1000},
    ]})
    assert find_by_name_ci(hal, "Martin") is None
