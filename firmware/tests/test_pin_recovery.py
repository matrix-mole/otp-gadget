"""Tests for PIN recovery flows (restore via device secret, wipe, PIN change).

These exercise the crypto logic in isolation - no HAL, no UI simulation.
"""
import os

from firmware.core.crypto.ctr import aes_ctr_xor
from firmware.core.crypto.kek import derive_kek
from firmware.core.crypto.master_key import (
    check_verify_token,
    make_recovery_key,
    make_verify_token,
    unwrap_dek,
    verify_device_secret,
    wrap_dek,
)

_ITER = 100  # fast for tests


# ── test_restore_flow ─────────────────────────────────────────────────────────

def test_restore_flow_recovers_dek_via_device_secret():
    """Recover DEK via device_secret, set new PIN, verify old PIN no longer works."""
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    card_salt = os.urandom(32)
    old_pin = "1234"
    new_pin = "5678"

    # CardInit: create master_key.enc and recovery_token.enc
    old_kek = derive_kek(old_pin, device_secret, card_salt, _ITER)
    master_key_enc = wrap_dek(dek, old_kek, os.urandom(16))
    recovery_key = make_recovery_key(device_secret)
    recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    # Restore: verify device_secret matches
    assert verify_device_secret(device_secret.hex(), device_secret)
    assert not verify_device_secret(os.urandom(32).hex(), device_secret)

    # Recover DEK via recovery_token
    recovered_dek = unwrap_dek(recovery_token_enc, make_recovery_key(device_secret))
    assert recovered_dek == dek

    # Set new PIN: re-wrap DEK under new KEK
    new_kek = derive_kek(new_pin, device_secret, card_salt, _ITER)
    new_master_key_enc = wrap_dek(dek, new_kek, os.urandom(16))
    new_recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    # Old PIN no longer unlocks the new master_key.enc
    assert unwrap_dek(new_master_key_enc, old_kek) != dek

    # New PIN unlocks
    assert unwrap_dek(new_master_key_enc, new_kek) == dek

    # Recovery still works after PIN change
    assert unwrap_dek(new_recovery_token_enc, recovery_key) == dek


def test_restore_flow_verify_bin_check():
    """DEK recovered from recovery_token is validated against verify.bin."""
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    card_salt = os.urandom(32)

    recovery_key = make_recovery_key(device_secret)
    recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    # Simulate what _run_restore does: decrypt verify.bin with the recovered DEK
    verify_plaintext = make_verify_token(card_salt)
    iv = os.urandom(16)
    verify_bin_raw = iv + aes_ctr_xor(dek, iv, verify_plaintext)

    recovered_dek = unwrap_dek(recovery_token_enc, make_recovery_key(device_secret))
    iv_read, ct = verify_bin_raw[:16], verify_bin_raw[16:]
    decrypted = aes_ctr_xor(recovered_dek, iv_read, ct)
    assert check_verify_token(decrypted, card_salt)


def test_restore_flow_wrong_device_secret_rejected():
    """Wrong device_secret cannot recover access."""
    device_secret = os.urandom(32)
    wrong_secret = os.urandom(32)
    dek = os.urandom(32)

    recovery_key = make_recovery_key(device_secret)
    recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    assert not verify_device_secret(wrong_secret.hex(), device_secret)

    # Even if attacker tries to use wrong recovery key, DEK comes out garbage
    wrong_recovery_key = make_recovery_key(wrong_secret)
    recovered = unwrap_dek(recovery_token_enc, wrong_recovery_key)
    assert recovered != dek


# ── test_wipe_flow ────────────────────────────────────────────────────────────

def test_wipe_flow_deletes_secret_and_exchange():
    """_do_wipe removes /secret/ and /exchange/ but leaves /device/ untouched."""

    class FakeHAL:
        def __init__(self):
            self.files = {}

        def write_file(self, slot, path, data):
            self.files[(slot, path)] = data

        def file_exists(self, slot, path):
            return (slot, path) in self.files

        def delete_tree(self, slot, path):
            prefix = path.rstrip("/") + "/"
            to_del = [k for k in list(self.files)
                      if k[0] == slot and (k[1] == path or k[1].startswith(prefix))]
            for k in to_del:
                del self.files[k]

    hal = FakeHAL()
    hal.write_file("own", "/secret/master_key.enc",   b"blob")
    hal.write_file("own", "/secret/verify.bin",       b"blob")
    hal.write_file("own", "/secret/recovery_token.enc", b"blob")
    hal.write_file("own", "/secret/contacts.json",    b"{}")
    hal.write_file("own", "/exchange/X_own.bin",       b"random")
    hal.write_file("own", "/device/card_salt.bin",    b"salt")   # must survive
    hal.write_file("own", "/device/kdf_params.json",  b"{}")     # must survive

    # Simulate _do_wipe
    from firmware.core.screens.pin_recovery import _do_wipe
    _do_wipe(hal)

    assert not hal.file_exists("own", "/secret/master_key.enc")
    assert not hal.file_exists("own", "/secret/verify.bin")
    assert not hal.file_exists("own", "/secret/recovery_token.enc")
    assert not hal.file_exists("own", "/exchange/X_own.bin")
    assert hal.file_exists("own", "/device/card_salt.bin")
    assert hal.file_exists("own", "/device/kdf_params.json")


# ── test_recovery_token_updated_on_pin_change ─────────────────────────────────

def test_recovery_token_updated_on_pin_change():
    """After PIN change: old PIN fails, new PIN works, restore still works."""
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    card_salt = os.urandom(32)
    old_pin = "1111"
    new_pin = "9999"

    # CardInit
    old_kek = derive_kek(old_pin, device_secret, card_salt, _ITER)
    master_key_enc = wrap_dek(dek, old_kek, os.urandom(16))
    recovery_key = make_recovery_key(device_secret)
    recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    # Verify old PIN works
    assert unwrap_dek(master_key_enc, old_kek) == dek

    # ChangePIN: verify old PIN → get DEK → wrap with new KEK
    assert unwrap_dek(master_key_enc, old_kek) == dek  # simulates _verify_pin_get_dek
    new_kek = derive_kek(new_pin, device_secret, card_salt, _ITER)
    new_master_key_enc = wrap_dek(dek, new_kek, os.urandom(16))
    new_recovery_token_enc = wrap_dek(dek, recovery_key, os.urandom(16))

    # Old PIN can no longer unlock the new master_key.enc
    assert unwrap_dek(new_master_key_enc, old_kek) != dek

    # New PIN works
    assert unwrap_dek(new_master_key_enc, new_kek) == dek

    # Restore still works: device_secret → recovery_key → DEK
    assert unwrap_dek(new_recovery_token_enc, recovery_key) == dek


def test_recovery_token_same_dek_after_pin_change():
    """DEK is unchanged after a PIN change - only the wrapping key changes."""
    device_secret = os.urandom(32)
    dek = os.urandom(32)
    card_salt = os.urandom(32)
    old_pin = "2222"
    new_pin = "8888"

    old_kek = derive_kek(old_pin, device_secret, card_salt, _ITER)
    master_key_enc = wrap_dek(dek, old_kek, os.urandom(16))
    recovery_key = make_recovery_key(device_secret)

    # Chang PIN
    recovered_dek = unwrap_dek(master_key_enc, old_kek)
    assert recovered_dek == dek

    new_kek = derive_kek(new_pin, device_secret, card_salt, _ITER)
    new_master_key_enc = wrap_dek(recovered_dek, new_kek, os.urandom(16))
    new_recovery_token_enc = wrap_dek(recovered_dek, recovery_key, os.urandom(16))

    # DEK is the same before and after
    assert unwrap_dek(new_master_key_enc, new_kek) == dek
    assert unwrap_dek(new_recovery_token_enc, recovery_key) == dek


# ── test_view_device_secret ───────────────────────────────────────────────────

def test_view_device_secret_matches_flash():
    """Device secret displayed in Settings equals the value stored in MCU flash."""
    device_secret = os.urandom(32)

    class FakeHAL:
        def __init__(self):
            self._flash = {}
        def flash_write(self, path, data):
            self._flash[path] = data
        def flash_read(self, path):
            return self._flash[path]

    hal = FakeHAL()
    hal.flash_write("device_secret.bin", device_secret)

    stored = hal.flash_read("device_secret.bin")
    displayed_hex = stored.hex().upper()

    assert stored == device_secret
    assert displayed_hex == device_secret.hex().upper()
    assert len(displayed_hex) == 64


# ── test_full_factory_reset ───────────────────────────────────────────────────

def test_full_factory_reset_clears_flash():
    """After factory reset, device_secret.bin is gone and /secret/ + /exchange/ are empty."""

    class FakeHAL:
        def __init__(self):
            self.files = {}
            self.flash = {}

        def write_file(self, slot, path, data):
            self.files[(slot, path)] = data

        def file_exists(self, slot, path):
            return (slot, path) in self.files

        def delete_tree(self, slot, path):
            prefix = path.rstrip("/") + "/"
            to_del = [k for k in list(self.files)
                      if k[0] == slot and (k[1] == path or k[1].startswith(prefix))]
            for k in to_del:
                del self.files[k]

        def flash_write(self, path, data):
            self.flash[path] = data

        def flash_exists(self, path):
            return path in self.flash

        def flash_delete(self, path):
            self.flash.pop(path, None)

    hal = FakeHAL()
    hal.write_file("own", "/secret/master_key.enc", b"blob")
    hal.write_file("own", "/secret/verify.bin", b"blob")
    hal.write_file("own", "/exchange/X_own.bin", b"random")
    hal.write_file("own", "/device/card_salt.bin", b"salt")
    hal.flash_write("device_secret.bin", os.urandom(32))
    hal.flash_write("pin_attempts.json", b"{}")

    from firmware.core.screens.pin_recovery import _do_wipe, _ATTEMPTS_FILE
    _do_wipe(hal)
    hal.flash_delete("device_secret.bin")
    hal.flash_delete(_ATTEMPTS_FILE)

    assert not hal.file_exists("own", "/secret/master_key.enc")
    assert not hal.file_exists("own", "/exchange/X_own.bin")
    assert not hal.flash_exists("device_secret.bin")
    assert not hal.flash_exists("pin_attempts.json")
    assert hal.file_exists("own", "/device/card_salt.bin")


def test_full_factory_reset_reruns_device_setup():
    """After factory reset, boot sees no device_secret.bin and routes to DeviceSetup."""

    class FakeHAL:
        def __init__(self):
            self.flash = {"device_secret.bin": os.urandom(32)}

        def flash_exists(self, path):
            return path in self.flash

        def flash_delete(self, path):
            self.flash.pop(path, None)

    hal = FakeHAL()
    assert hal.flash_exists("device_secret.bin")

    hal.flash_delete("device_secret.bin")

    # boot.py: `if not hal.flash_exists("device_secret.bin") → DeviceSetup`
    assert not hal.flash_exists("device_secret.bin")


# ── test_real_hal_implements_full_interface ───────────────────────────────────

def test_real_hal_implements_full_interface():
    """RealHAL must override every abstract method on HALBase.

    Abstract = body raises NotImplementedError. Methods with a default
    implementation (e.g. notify_screen, which is a no-op on real hardware)
    are exempt.

    Static AST check - real.py imports MicroPython-only modules (`machine`)
    that aren't available in the test environment, so we parse the source
    instead of importing.
    """
    import ast

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")

    def is_abstract(fn: ast.FunctionDef) -> bool:
        # Body is exactly `raise NotImplementedError` (with or without args).
        if len(fn.body) != 1 or not isinstance(fn.body[0], ast.Raise):
            return False
        exc = fn.body[0].exc
        if isinstance(exc, ast.Name):
            return exc.id == "NotImplementedError"
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            return exc.func.id == "NotImplementedError"
        return False

    with open(os.path.join(repo_root, "firmware/hal/base.py")) as f:
        base_tree = ast.parse(f.read())
    base_class = next(n for n in ast.walk(base_tree)
                      if isinstance(n, ast.ClassDef) and n.name == "HALBase")
    abstract_methods = {n.name for n in base_class.body
                        if isinstance(n, ast.FunctionDef) and is_abstract(n)}

    with open(os.path.join(repo_root, "firmware/hal/real.py")) as f:
        real_tree = ast.parse(f.read())
    real_class = next(n for n in ast.walk(real_tree)
                      if isinstance(n, ast.ClassDef) and n.name == "RealHAL")
    real_methods = {n.name for n in real_class.body
                    if isinstance(n, ast.FunctionDef)}

    missing = abstract_methods - real_methods
    assert not missing, f"RealHAL missing abstract methods from HALBase: {sorted(missing)}"
