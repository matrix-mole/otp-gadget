import os
import pytest
from firmware.core.crypto.master_key import (
    wrap_dek,
    unwrap_dek,
    make_verify_token,
    check_verify_token,
    make_recovery_key,
    verify_device_secret,
    VERIFY_MAGIC,
)


# ── verify token ──────────────────────────────────────────────────────────────

def test_make_verify_token_starts_with_magic():
    salt = os.urandom(32)
    token = make_verify_token(salt)
    assert token.startswith(VERIFY_MAGIC)


def test_make_verify_token_contains_salt():
    salt = os.urandom(32)
    token = make_verify_token(salt)
    assert salt in token


def test_check_verify_token_correct():
    salt = os.urandom(32)
    token = make_verify_token(salt)
    assert check_verify_token(token, salt)


def test_check_verify_token_wrong_salt():
    salt = os.urandom(32)
    token = make_verify_token(salt)
    wrong_salt = os.urandom(32)
    assert not check_verify_token(token, wrong_salt)


def test_check_verify_token_truncated():
    salt = os.urandom(32)
    token = make_verify_token(salt)
    assert not check_verify_token(token[:3], salt)


def test_check_verify_token_all_zeros():
    salt = b"\x00" * 32
    token = make_verify_token(salt)
    assert check_verify_token(token, salt)


# ── DEK wrap / unwrap ─────────────────────────────────────────────────────────

def test_wrap_unwrap_roundtrip():
    kek = os.urandom(32)
    dek = os.urandom(32)
    iv  = os.urandom(16)
    blob = wrap_dek(dek, kek, iv)
    assert unwrap_dek(blob, kek) == dek


def test_wrap_output_length():
    blob = wrap_dek(b"\x00" * 32, b"\x00" * 32, b"\x00" * 16)
    assert len(blob) == 48  # 16-byte IV + 32-byte ciphertext


def test_wrap_wrong_iv_length():
    with pytest.raises(ValueError, match="IV must be 16 bytes"):
        wrap_dek(b"\x00" * 32, b"\x00" * 32, b"\x00" * 8)


def test_unwrap_wrong_blob_length():
    with pytest.raises(ValueError):
        unwrap_dek(b"\x00" * 32, b"\x00" * 32)


def test_unwrap_wrong_kek_returns_garbage():
    kek = os.urandom(32)
    dek = os.urandom(32)
    iv  = os.urandom(16)
    blob = wrap_dek(dek, kek, iv)
    wrong_kek = os.urandom(32)
    recovered = unwrap_dek(blob, wrong_kek)
    assert recovered != dek


def test_different_ivs_produce_different_blobs():
    kek = b"\xAB" * 32
    dek = b"\xCD" * 32
    blob1 = wrap_dek(dek, kek, b"\x00" * 16)
    blob2 = wrap_dek(dek, kek, b"\x01" * 16)
    assert blob1 != blob2


# ── recovery key ──────────────────────────────────────────────────────────────

def test_make_recovery_key_is_32_bytes():
    assert len(make_recovery_key(os.urandom(32))) == 32


def test_make_recovery_key_deterministic():
    secret = os.urandom(32)
    assert make_recovery_key(secret) == make_recovery_key(secret)


def test_make_recovery_key_differs_by_secret():
    assert make_recovery_key(os.urandom(32)) != make_recovery_key(os.urandom(32))


def test_recovery_token_roundtrip():
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    iv = os.urandom(16)
    recovery_key = make_recovery_key(device_secret)
    blob = wrap_dek(dek, recovery_key, iv)
    assert unwrap_dek(blob, recovery_key) == dek


def test_recovery_token_wrong_secret_gives_garbage():
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    iv = os.urandom(16)
    recovery_key = make_recovery_key(device_secret)
    blob = wrap_dek(dek, recovery_key, iv)
    wrong_key = make_recovery_key(os.urandom(32))
    assert unwrap_dek(blob, wrong_key) != dek


# ── verify_device_secret ──────────────────────────────────────────────────────

def test_verify_device_secret_correct():
    secret = os.urandom(32)
    assert verify_device_secret(secret.hex(), secret)


def test_verify_device_secret_wrong():
    secret = os.urandom(32)
    wrong = os.urandom(32)
    assert not verify_device_secret(wrong.hex(), secret)


def test_verify_device_secret_invalid_hex():
    secret = os.urandom(32)
    assert not verify_device_secret("not-hex!!", secret)


def test_verify_device_secret_empty_string():
    assert not verify_device_secret("", os.urandom(32))


def test_verify_device_secret_uppercase_and_lowercase():
    secret = os.urandom(32)
    assert verify_device_secret(secret.hex().upper(), secret)
    assert verify_device_secret(secret.hex().lower(), secret)
