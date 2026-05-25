import pytest
from firmware.core.crypto.mac import compute_tag, verify_tag


def test_compute_tag_length():
    tag = compute_tag(b"\x00" * 8, 0, 5, b"hello")
    assert len(tag) == 8


def test_compute_tag_deterministic():
    mac_key = bytes(range(8))
    t1 = compute_tag(mac_key, 100, 5, b"hello")
    t2 = compute_tag(mac_key, 100, 5, b"hello")
    assert t1 == t2


def test_verify_tag_correct():
    mac_key = bytes(range(8))
    ciphertext = b"\x01\x02\x03"
    tag = compute_tag(mac_key, 0, len(ciphertext), ciphertext)
    assert verify_tag(mac_key, 0, len(ciphertext), ciphertext, tag)


def test_verify_tag_wrong_ciphertext():
    mac_key = bytes(range(8))
    ciphertext = b"\x01\x02\x03"
    tag = compute_tag(mac_key, 0, len(ciphertext), ciphertext)
    tampered = bytes(b ^ 0xFF for b in ciphertext)
    assert not verify_tag(mac_key, 0, len(ciphertext), tampered, tag)


def test_verify_tag_wrong_key():
    ciphertext = b"secret"
    tag = compute_tag(b"\x01" * 8, 0, len(ciphertext), ciphertext)
    assert not verify_tag(b"\x02" * 8, 0, len(ciphertext), ciphertext, tag)


def test_verify_tag_wrong_offset():
    mac_key = b"\xAB" * 8
    ciphertext = b"data"
    tag = compute_tag(mac_key, 500, len(ciphertext), ciphertext)
    assert not verify_tag(mac_key, 501, len(ciphertext), ciphertext, tag)


def test_verify_tag_truncated_tag():
    mac_key = b"\x00" * 8
    ciphertext = b"x"
    tag = compute_tag(mac_key, 0, 1, ciphertext)
    assert not verify_tag(mac_key, 0, 1, ciphertext, tag[:7])


def test_compute_tag_different_offsets_differ():
    mac_key = b"\x00" * 8
    ct = b"hello"
    t1 = compute_tag(mac_key, 0, len(ct), ct)
    t2 = compute_tag(mac_key, 1, len(ct), ct)
    assert t1 != t2
