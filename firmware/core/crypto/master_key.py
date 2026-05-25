from firmware.core.crypto.ctr import aes_ctr_xor as _aes_ctr_xor
from firmware.core.crypto.kek import hmac_sha256 as _hmac_sha256


def _aes_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    return _aes_ctr_xor(key, iv, data)


VERIFY_MAGIC = b"OTPG_V1"
_RECOVERY_TAG = b"RECOVERY"


def wrap_dek(dek: bytes, kek: bytes, iv: bytes) -> bytes:
    """Return on-disk bytes for master_key.enc: [IV (16)] [AES-CTR(kek, dek)]."""
    if len(iv) != 16:
        raise ValueError("IV must be 16 bytes")
    return iv + _aes_ctr(kek, iv, dek)


def unwrap_dek(raw: bytes, kek: bytes) -> bytes:
    """Parse master_key.enc bytes and return the decrypted DEK."""
    if len(raw) != 48:
        raise ValueError(f"master_key.enc must be 48 bytes, got {len(raw)}")
    iv, ct = raw[:16], raw[16:]
    return _aes_ctr(kek, iv, ct)


def _ct_eq(a: bytes, b: bytes) -> bool:
    """Constant-time equality - avoids timing oracle on PIN verification."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


def make_recovery_key(device_secret: bytes) -> bytes:
    """Derive a 32-byte recovery key from the device secret via HMAC-SHA256."""
    return _hmac_sha256(device_secret, _RECOVERY_TAG)


def verify_device_secret(entered_hex: str, stored_secret: bytes) -> bool:
    """Constant-time check: does the entered hex string match the stored device_secret?"""
    try:
        entered = bytes.fromhex(entered_hex)
    except (ValueError, TypeError):
        return False
    return _ct_eq(entered, stored_secret)


def make_verify_token(card_salt: bytes) -> bytes:
    """Plaintext written into verify.bin at card init (encrypted with DEK by the HAL)."""
    return VERIFY_MAGIC + card_salt


def check_verify_token(plaintext: bytes, card_salt: bytes) -> bool:
    """True if a decrypted verify.bin matches the expected token."""
    expected = make_verify_token(card_salt)
    return _ct_eq(plaintext[: len(expected)], expected)
