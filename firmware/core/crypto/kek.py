import hashlib


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    BLOCK = 64
    if len(key) > BLOCK:
        key = hashlib.sha256(key).digest()
    key = key + b"\x00" * (BLOCK - len(key))
    o_key = bytes(b ^ 0x5C for b in key)
    i_key = bytes(b ^ 0x36 for b in key)
    return hashlib.sha256(o_key + hashlib.sha256(i_key + msg).digest()).digest()


def _pbkdf2_hmac_sha256(password: bytes, salt: bytes, iterations: int) -> bytes:
    # Single 32-byte output block - sufficient since dk_len is always 32.
    # Pure Python so it runs unchanged on MicroPython (no built-in PBKDF2).
    u = hmac_sha256(password, salt + b"\x00\x00\x00\x01")
    out = bytearray(u)
    for _ in range(iterations - 1):
        u = hmac_sha256(password, u)
        for i in range(32):
            out[i] ^= u[i]
    return bytes(out)


def derive_kek(pin: str, device_secret: bytes, card_salt: bytes, iterations: int) -> bytes:
    """Return 32-byte KEK = PBKDF2-HMAC-SHA256(PIN || device_secret, salt=card_salt)."""
    password = pin.encode("ascii") + device_secret
    return _pbkdf2_hmac_sha256(password, card_salt, iterations)
