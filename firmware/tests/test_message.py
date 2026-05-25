import pytest
from firmware.core.message import xor_pad, encode, decode


def test_xor_pad_roundtrip():
    plaintext = b"hello world!!"
    pad = bytes(range(len(plaintext)))
    ciphertext = xor_pad(plaintext, pad)
    assert xor_pad(ciphertext, pad) == plaintext


def test_xor_pad_all_zeros():
    data = b"\x01\x02\x03"
    assert xor_pad(data, b"\x00\x00\x00") == data


def test_xor_pad_length_mismatch():
    with pytest.raises(ValueError, match="pad length mismatch"):
        xor_pad(b"abc", b"ab")


def test_xor_pad_same_bytes():
    data = b"\xAB\xCD"
    assert xor_pad(data, data) == b"\x00\x00"


def test_encode_decode_roundtrip():
    offset = 1234
    ciphertext = b"\x01\x02\x03\x04\x05"
    tag = b"\xAA\xBB\xCC\xDD\xEE\xFF\x11\x22"
    hex_str = encode(offset, ciphertext, tag)
    out_offset, out_ct, out_tag = decode(hex_str)
    assert out_offset == offset
    assert out_ct == ciphertext
    assert out_tag == tag


def test_encode_is_uppercase_hex():
    hex_str = encode(0, b"\xde\xad", b"\x00" * 8)
    assert hex_str == hex_str.upper()
    bytes.fromhex(hex_str)  # must be valid hex


def test_decode_offset_zero():
    offset = 0
    ciphertext = b"A" * 10
    tag = bytes(range(8))
    out_offset, out_ct, out_tag = decode(encode(offset, ciphertext, tag))
    assert out_offset == 0
    assert out_ct == ciphertext


def test_decode_max_offset():
    offset = 0xFFFFFFFF  # max uint32
    ciphertext = b"\xFF"
    tag = b"\x00" * 8
    out_offset, _, _ = decode(encode(offset, ciphertext, tag))
    assert out_offset == offset


def test_decode_invalid_hex():
    with pytest.raises(ValueError, match="invalid hex"):
        decode("ZZZZ")


def test_decode_frame_too_short():
    with pytest.raises(ValueError, match="frame too short"):
        decode("AABBCC")


def test_decode_length_mismatch():
    # Build a frame then corrupt the length field
    hex_str = encode(0, b"hello", b"\x00" * 8)
    raw = bytearray(bytes.fromhex(hex_str))
    raw[5] = 99  # length byte → mismatch
    with pytest.raises(ValueError, match="frame length mismatch"):
        decode(raw.hex().upper())
