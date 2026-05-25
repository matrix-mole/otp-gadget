"""Tests for the _trial_decrypt routing logic in receive.py.

Covers:
- Correct contact picked when 3+ contacts exist
- Iteration in created_at order (first-match-wins)
- Contacts with missing pad_receive.bin are skipped
- Wrong-contact frame → "Authentication failed" (wire format unchanged)
- Empty contact list → "Authentication failed"
- Bad hex format → "Bad format" error
- Used range recorded after successful decrypt
- Replay detection on second decode of the same frame
"""
import json
import os

from firmware.core.crypto.mac import compute_tag
from firmware.core.message import encode, xor_pad
from firmware.core.screens.receive import _trial_decrypt

_MANIFEST_PATH = "/secret/contacts.json"


class _FakeHal:
    def __init__(self):
        self._secrets = {}
        self._files = set()    # (slot, path) -> registered
        self._pads = {}        # secret_path -> bytes
        self.overwrites = []

    def read_secret(self, path):
        if path not in self._secrets:
            raise OSError(f"not found: {path}")
        return self._secrets[path]

    def write_secret(self, path, data):
        self._secrets[path] = data

    def file_exists(self, slot, path):
        return (slot, path) in self._files

    def read_secret_slice(self, path, offset, length):
        if path not in self._pads:
            raise OSError(f"no pad: {path}")
        buf = self._pads[path]
        if offset + length > len(buf):
            raise OSError("slice out of range")
        return buf[offset : offset + length]

    def overwrite_secret_slice(self, path, offset, data):
        buf = self._pads[path]
        self._pads[path] = buf[:offset] + data + buf[offset + len(data):]
        self.overwrites.append((path, offset, data))

    def get_random_bytes(self, n):
        return b"\x00" * n

    # ── setup helpers ─────────────────────────────────────────────────────────

    def _put_manifest(self, manifest: dict):
        self._secrets[_MANIFEST_PATH] = json.dumps(manifest).encode("utf-8")

    def _add_contact(self, cid: str, name: str, created_at: int, pad_bytes: bytes):
        """Register a contact in the manifest and install its receive pad."""
        try:
            m = json.loads(self._secrets[_MANIFEST_PATH])
        except KeyError:
            m = {"version": 1, "in_flight": None, "contacts": []}
        m["contacts"].append({"id": cid, "name": name, "created_at": created_at})
        self._secrets[_MANIFEST_PATH] = json.dumps(m).encode("utf-8")

        recv = f"/secret/contacts/{cid}/pad_receive.bin"
        send = f"/secret/contacts/{cid}/pad_send.bin"
        self._pads[recv] = pad_bytes
        self._files.add(("own", recv))
        self._files.add(("own", send))

    def _add_contact_no_pad(self, cid: str, name: str, created_at: int):
        """Register a contact but leave its pad files absent (simulates missing pads)."""
        try:
            m = json.loads(self._secrets[_MANIFEST_PATH])
        except KeyError:
            m = {"version": 1, "in_flight": None, "contacts": []}
        m["contacts"].append({"id": cid, "name": name, "created_at": created_at})
        self._secrets[_MANIFEST_PATH] = json.dumps(m).encode("utf-8")
        # No files added - pads_valid will return False; read_secret_slice raises OSError


def _make_frame(pad_bytes: bytes, offset: int, plaintext: str) -> str:
    """Build a valid wire-format hex frame using the given pad and plaintext."""
    pt = plaintext.encode("ascii")
    n = len(pt)
    ct = xor_pad(pt, pad_bytes[offset : offset + n])
    mac_key = pad_bytes[offset + n : offset + n + 8]
    tag = compute_tag(mac_key, offset, n, ct)
    return encode(offset, ct, tag)


def _rnd(size: int = 1024) -> bytes:
    return os.urandom(size)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_trial_decrypt_picks_correct_contact_among_three():
    pad_alice = _rnd()
    pad_bob   = _rnd()
    pad_carol = _rnd()

    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad_alice)
    hal._add_contact("bbbbbbbb", "Bob",   2000, pad_bob)
    hal._add_contact("cccccccc", "Carol", 3000, pad_carol)

    hex_str = _make_frame(pad_carol, 0, "hello from carol")
    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert err == ""
    assert cid == "cccccccc"
    assert name == "Carol"
    assert plaintext == "hello from carol"
    assert not replay


def test_trial_decrypt_iteration_order_oldest_first():
    """Contacts iterated created_at ascending; correct contact found regardless of dict order."""
    pad_alice = _rnd()
    pad_bob   = _rnd()

    hal = _FakeHal()
    # Bob has smaller created_at → iterated first; Alice second
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("bbbbbbbb", "Bob",   1000, pad_bob)
    hal._add_contact("aaaaaaaa", "Alice", 2000, pad_alice)

    # Frame from Alice (second in order) - Bob's HMAC won't match
    hex_str = _make_frame(pad_alice, 0, "order test")
    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert err == ""
    assert cid == "aaaaaaaa"
    assert name == "Alice"


def test_trial_decrypt_skips_contact_with_missing_pads():
    pad_alice = _rnd()   # Alice's pad absent; Bob's pad present
    pad_bob   = _rnd()

    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact_no_pad("aaaaaaaa", "Alice", 1000)   # no pad files
    hal._add_contact("bbbbbbbb", "Bob", 2000, pad_bob)

    hex_str = _make_frame(pad_bob, 0, "skip alice")
    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert err == ""
    assert cid == "bbbbbbbb"
    assert name == "Bob"
    assert plaintext == "skip alice"


def test_trial_decrypt_wrong_contact_returns_auth_failed():
    """Frame encrypted with Bob's pad should fail when only Alice is registered."""
    pad_alice = _rnd()
    pad_bob   = _rnd()   # not registered

    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad_alice)

    hex_str = _make_frame(pad_bob, 0, "wrong contact")
    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert cid is None
    assert "unknown sender" in err


def test_trial_decrypt_empty_contacts_returns_auth_failed():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})

    hex_str = _make_frame(_rnd(), 0, "hi")
    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert cid is None
    assert "unknown sender" in err


def test_trial_decrypt_bad_format_returns_error():
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})

    cid, name, plaintext, replay, err = _trial_decrypt(hal, "ZZZZZZ")

    assert cid is None
    assert "Invalid code format" in err


def test_trial_decrypt_records_used_range():
    offset = 0
    plaintext = "hello"
    pad = _rnd()
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad)

    hex_str = _make_frame(pad, offset, plaintext)
    _trial_decrypt(hal, hex_str)

    from firmware.core.bookkeeping import read_used_ranges
    ranges = read_used_ranges(hal, "aaaaaaaa")
    assert len(ranges) == 1
    start, end = ranges[0]
    assert start == offset
    assert end == offset + len(plaintext) + 8   # ciphertext + MAC key bytes


def test_trial_decrypt_non_zero_offset():
    """Mid-pad usage: offset > 0 is the normal steady-state case after first messages."""
    offset = 512
    plaintext = "mid-pad message"
    pad = _rnd(1024)
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad)

    hex_str = _make_frame(pad, offset, plaintext)
    cid, name, decoded, replay, err = _trial_decrypt(hal, hex_str)

    assert err == ""
    assert cid == "aaaaaaaa"
    assert decoded == plaintext

    from firmware.core.bookkeeping import read_used_ranges
    ranges = read_used_ranges(hal, "aaaaaaaa")
    assert ranges == [[offset, offset + len(plaintext) + 8]]


def test_trial_decrypt_non_ascii_returns_error():
    """If pad XOR produces non-ASCII bytes the decode-error path fires."""
    pad = _rnd()
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad)

    # Build a frame whose plaintext is raw non-ASCII bytes (e.g. 0xFF bytes).
    # _trial_decrypt will validate the HMAC (match), then fail .decode("ascii").
    raw_pt = bytes([0xFF, 0xFE, 0xFD, 0x80])
    n = len(raw_pt)
    ct = xor_pad(raw_pt, pad[0:n])
    mac_key = pad[n : n + 8]
    tag = compute_tag(mac_key, 0, n, ct)
    hex_str = encode(0, ct, tag)

    cid, name, plaintext, replay, err = _trial_decrypt(hal, hex_str)

    assert "unsupported characters" in err
    assert plaintext is None
    assert cid == "aaaaaaaa"   # contact was identified before decode error


def test_trial_decrypt_replay_detection():
    pad = _rnd()
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad)

    hex_str = _make_frame(pad, 0, "replay me")

    _, _, _, replay1, err1 = _trial_decrypt(hal, hex_str)
    assert err1 == ""
    assert not replay1

    _, _, _, replay2, err2 = _trial_decrypt(hal, hex_str)
    assert err2 == ""
    assert replay2


def test_trial_decrypt_burn_after_reading_scrubs_pad_and_blocks_replay():
    pad = _rnd()
    hal = _FakeHal()
    hal._put_manifest({"version": 1, "in_flight": None, "contacts": []})
    hal._add_contact("aaaaaaaa", "Alice", 1000, pad)

    plaintext = "burn me"
    hex_str = _make_frame(pad, 0, plaintext)

    cid, name, decoded, replay, err = _trial_decrypt(hal, hex_str, burn_after_reading=True)
    assert err == ""
    assert cid == "aaaaaaaa"
    assert name == "Alice"
    assert decoded == plaintext
    assert not replay

    recv_path = "/secret/contacts/aaaaaaaa/pad_receive.bin"
    assert hal.overwrites == [(recv_path, 0, b"\x00" * (len(plaintext) + 8))]

    cid2, name2, decoded2, replay2, err2 = _trial_decrypt(hal, hex_str, burn_after_reading=True)
    assert cid2 is None
    assert name2 is None
    assert decoded2 is None
    assert not replay2
    assert err2
