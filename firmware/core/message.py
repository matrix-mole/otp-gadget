import struct


def xor_pad(data: bytes, pad: bytes) -> bytes:
    if len(data) != len(pad):
        raise ValueError("pad length mismatch")
    return bytes(a ^ b for a, b in zip(data, pad))


def encode(offset: int, ciphertext: bytes, tag: bytes) -> str:
    frame = struct.pack(">IH", offset, len(ciphertext)) + ciphertext + tag
    return frame.hex().upper()


def decode(hex_str: str) -> tuple:
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        raise ValueError("invalid hex")
    if len(raw) < 14:
        raise ValueError("frame too short")
    offset, length = struct.unpack(">IH", raw[:6])
    if len(raw) != 6 + length + 8:
        raise ValueError("frame length mismatch")
    return offset, raw[6 : 6 + length], raw[6 + length :]
