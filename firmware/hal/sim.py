import base64
import os
import shutil
import threading

from Crypto.Cipher import AES
from Crypto.Util import Counter

from firmware.hal.base import HALBase
from firmware.sim.app import socketio

CHUNK = 4096


class _PoweredOff(BaseException):
    """Raised by HAL calls when the simulated device has been powered off.

    Inherits BaseException so it passes through any bare `except Exception` blocks
    in core screens and unwinds the full call stack to the thread wrapper in app.py.
    """


_CTR_MOD = 1 << 128


def _make_ctr(iv: bytes, block_index: int = 0) -> object:
    return Counter.new(128, initial_value=(int.from_bytes(iv, "big") + block_index) % _CTR_MOD)


class WriteHandle:
    """Atomic write: buffers to .tmp, renames to final on close."""

    def __init__(self, final_path: str) -> None:
        self._final = final_path
        self._tmp = final_path + ".tmp"
        os.makedirs(os.path.dirname(self._tmp) or ".", exist_ok=True)
        self._f = open(self._tmp, "wb")

    def write(self, data: bytes) -> None:
        self._f.write(data)

    def seek(self, offset: int) -> None:
        self._f.seek(offset)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._f.close()
        except OSError:
            pass
        if exc_type is None:
            os.replace(self._tmp, self._final)
        else:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
        return False

    def close(self) -> None:
        self._f.close()
        os.replace(self._tmp, self._final)


class EncryptedWriteHandle:
    """Atomic encrypted write: prepends fresh IV, encrypts on write, renames on close."""

    def __init__(self, final_path: str, key: bytes) -> None:
        self._final = final_path
        self._tmp = final_path + ".tmp"
        os.makedirs(os.path.dirname(self._tmp) or ".", exist_ok=True)
        iv = os.urandom(16)
        self._cipher = AES.new(key, AES.MODE_CTR, counter=_make_ctr(iv))
        self._f = open(self._tmp, "wb")
        self._f.write(iv)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._f.close()
        except OSError:
            pass
        if exc_type is None:
            os.replace(self._tmp, self._final)
        else:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
        return False

    def write(self, data: bytes) -> None:
        self._f.write(self._cipher.encrypt(data))

    def close(self) -> None:
        self._f.close()
        os.replace(self._tmp, self._final)


_PREUNLOCKED_DUMMY = b'\x00' * 32  # deterministic dummy for preunlocked sim sessions


class SimHAL(HALBase):

    def __init__(
        self,
        state_dir: str = "./sim_state",
        device_id: str = "",
        own_card_path: str | None = None,
        guest_card_path: str | None = None,
        preunlocked: bool = False,
        namespace: str = "/",
    ) -> None:
        self._state_dir = state_dir
        self._device_id = device_id
        self._own_card_path = own_card_path
        self._guest_card_path = guest_card_path
        self._namespace = namespace
        self._battery_pct = 80
        self._charging = True
        self._vbus = True
        self._dek: bytearray | None = None
        self._pending_touch = None
        self._pending_qr = None
        self._touch_lock = threading.Lock()
        self._qr_lock = threading.Lock()
        self._stopped = False
        self._error_pending = False
        self._preunlocked = preunlocked
        os.makedirs(os.path.join(state_dir, "mcu_flash"), exist_ok=True)
        if preunlocked:
            # Pre-set dummy DEK so secrets are accessible without PIN derivation.
            self._dek = bytearray(_PREUNLOCKED_DUMMY)
            self._ensure_preunlocked_flash()
            if own_card_path:
                self._ensure_preunlocked_card(own_card_path)

    def stop(self) -> None:
        """Signal the HAL to stop. Called by app.py on power-off.

        Sets _stopped=True so the next HAL call that touches user interaction
        raises _PoweredOff, unwinding the main_loop thread cleanly.
        """
        self._stopped = True

    # -- Sim-only setters (called by app.py event handlers) -------------------

    def _set_pending_touch(self, t) -> None:
        with self._touch_lock:
            self._pending_touch = t

    def _set_pending_qr(self, v) -> None:
        with self._qr_lock:
            self._pending_qr = v

    def _sim_trigger_error(self) -> None:
        self._error_pending = True

    def _ensure_preunlocked_flash(self) -> None:
        """Create MCU flash stub so boot.py's flash_exists check passes."""
        path = os.path.join(self._state_dir, "mcu_flash", "device_secret.bin")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(_PREUNLOCKED_DUMMY)

    def _ensure_preunlocked_card(self, card_path: str) -> None:
        """Create the minimum card files boot.py needs to skip DeviceSetup and CardInit."""
        verify_path = os.path.join(card_path, "secret", "verify.bin")
        if not os.path.exists(verify_path):
            from firmware.core.crypto.master_key import make_verify_token
            os.makedirs(os.path.dirname(verify_path), exist_ok=True)
            # verify.bin is only ever checked in PINEntryScreen._verify_pin, which is
            # skipped entirely in preunlocked sessions. Any valid-looking token works;
            # use the dummy bytes as the card_salt placeholder.
            dummy_card_salt = _PREUNLOCKED_DUMMY
            with open(verify_path, "wb") as f:
                f.write(make_verify_token(dummy_card_salt))

    def _sim_set_slot(self, slot: str, path: str | None) -> None:
        if slot == "own":
            self._own_card_path = path
            if self._preunlocked and path:
                self._ensure_preunlocked_card(path)
        elif slot == "guest":
            self._guest_card_path = path

    def _sim_set_battery(self, pct: int, charging: bool, vbus: bool | None = None) -> None:
        self._battery_pct = pct
        self._charging = charging
        self._vbus = charging if vbus is None else vbus

    # -- Internal helpers ------------------------------------------------------

    def _card_root(self, slot: str) -> str:
        if slot == "own":
            if not self._own_card_path:
                raise OSError("Own card not mounted")
            return self._own_card_path
        if slot == "guest":
            if not self._guest_card_path:
                raise OSError("No guest card mounted")
            return self._guest_card_path
        raise ValueError(f"Unknown slot: {slot!r}")

    def _card_file(self, slot: str, path: str) -> str:
        return os.path.join(self._card_root(slot), path.lstrip("/"))

    def _flash_file(self, path: str) -> str:
        return os.path.join(self._state_dir, "mcu_flash", path.lstrip("/"))

    # -- Display ---------------------------------------------------------------

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        if self._stopped:
            return
        socketio.emit("draw", {"op": "fill", "x": x, "y": y, "w": w, "h": h, "color": color}, room=self._device_id, namespace=self._namespace)

    def blit_rect(self, x: int, y: int, w: int, h: int, buf: bytes) -> None:
        if self._stopped:
            return
        socketio.emit("draw", {
            "op": "blit", "x": x, "y": y, "w": w, "h": h,
            "data": base64.b64encode(buf).decode(),
        }, room=self._device_id, namespace=self._namespace)

    # -- Touch -----------------------------------------------------------------

    def inject_touch(self, x: int, y: int) -> None:
        pass  # sim uses _set_pending_touch via the Flask layer

    def get_touch(self):  # -> tuple[int, int] | None
        if self._stopped:
            raise _PoweredOff()
        if self._error_pending:
            self._error_pending = False
            raise RuntimeError("Simulated error (test)")
        with self._touch_lock:
            t = self._pending_touch
            self._pending_touch = None
        return t

    # -- MCU flash -------------------------------------------------------------

    def flash_read(self, path: str) -> bytes:
        with open(self._flash_file(path), "rb") as f:
            return f.read()

    def flash_write(self, path: str, data: bytes) -> None:
        full = self._flash_file(path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        tmp = full + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, full)

    def flash_exists(self, path: str) -> bool:
        return os.path.exists(self._flash_file(path))

    def flash_delete(self, path: str) -> None:
        try:
            os.remove(self._flash_file(path))
        except OSError:
            pass

    # -- SD cards --------------------------------------------------------------

    def mount_card(self, slot: str) -> str:
        if slot == "own":
            if self._own_card_path and os.path.isdir(self._own_card_path):
                self._sweep_stale_tmp()
                return "MOUNTED"
            return "NO_CARD"
        if slot == "guest":
            return "MOUNTED" if (self._guest_card_path and os.path.isdir(self._guest_card_path)) else "NO_CARD"
        raise ValueError(f"Unknown slot: {slot!r}")

    def _sweep_stale_tmp(self) -> None:
        """Remove any *.tmp files left behind by writes that didn't reach rename.
        See RealHAL._sweep_stale_tmp for rationale."""
        root = self._own_card_path
        for sub in ("secret", "exchange"):
            self._sweep_dir(os.path.join(root, sub))
        contacts_dir = os.path.join(root, "secret", "contacts")
        if os.path.isdir(contacts_dir):
            for entry in os.listdir(contacts_dir):
                self._sweep_dir(os.path.join(contacts_dir, entry))

    def _sweep_dir(self, path: str) -> None:
        if not os.path.isdir(path):
            return
        for entry in os.listdir(path):
            if entry.endswith(".tmp"):
                try:
                    os.remove(os.path.join(path, entry))
                except OSError:
                    pass

    def unmount_card(self, slot: str) -> None:
        if slot == "own":
            self._own_card_path = None
        elif slot == "guest":
            self._guest_card_path = None

    def read_file(self, slot: str, path: str) -> bytes:
        with open(self._card_file(slot, path), "rb") as f:
            return f.read()

    def write_file(self, slot: str, path: str, data: bytes) -> None:
        full = self._card_file(slot, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        tmp = full + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, full)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def delete_file(self, slot: str, path: str) -> None:
        os.remove(self._card_file(slot, path))

    def delete_tree(self, slot: str, path: str) -> None:
        shutil.rmtree(self._card_file(slot, path), ignore_errors=True)

    def file_exists(self, slot: str, path: str) -> bool:
        return os.path.exists(self._card_file(slot, path))

    def free_space(self, slot: str) -> int:
        return shutil.disk_usage(self._card_root(slot)).free

    # -- Plaintext streaming ---------------------------------------------------

    def read_file_stream(self, slot: str, path: str, offset: int, length: int):
        full = self._card_file(slot, path)
        with open(full, "rb") as f:
            f.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)

    def write_file_stream(self, slot: str, path: str) -> WriteHandle:
        return WriteHandle(self._card_file(slot, path))

    # -- Entropy ---------------------------------------------------------------

    def get_random_bytes(self, n: int) -> bytes:
        return os.urandom(n)

    # -- Secrets ---------------------------------------------------------------

    def _require_dek(self) -> bytes:
        if self._dek is None:
            raise RuntimeError("Secrets not unlocked - call unlock_secrets() first")
        return bytes(self._dek)

    def unlock_secrets(self, key: bytes) -> None:
        if self._preunlocked:
            return  # DEK already set as dummy; ignore any derived key
        self._dek = bytearray(key)

    def lock_secrets(self) -> None:
        if self._preunlocked:
            # Auto-lock re-auth cycles call lock_secrets → PINEntry → unlock_secrets.
            # In preunlocked mode PINEntry is skipped, so the DEK must stay in RAM
            # to keep the session functional after every lock/re-auth cycle.
            return
        if self._dek is not None:
            for i in range(len(self._dek)):
                self._dek[i] = 0
        self._dek = None

    def read_secret(self, path: str) -> bytes:
        if self._preunlocked:
            with open(self._card_file("own", path), "rb") as f:
                return f.read()
        with open(self._card_file("own", path), "rb") as f:
            raw = f.read()
        iv, ct = raw[:16], raw[16:]
        return AES.new(self._require_dek(), AES.MODE_CTR, counter=_make_ctr(iv)).decrypt(ct)

    def read_secret_slice(self, path: str, offset: int, length: int) -> bytes:
        if self._preunlocked:
            with open(self._card_file("own", path), "rb") as f:
                f.seek(offset)
                return f.read(length)
        with open(self._card_file("own", path), "rb") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            f.seek(16 + block_index * 16)
            ct = f.read(block_offset + length)
        cipher = AES.new(self._require_dek(), AES.MODE_CTR, counter=_make_ctr(iv, block_index))
        pt = cipher.decrypt(ct)
        return pt[block_offset : block_offset + length]

    def overwrite_secret_slice(self, path: str, offset: int, data: bytes) -> None:
        full = self._card_file("own", path)
        if self._preunlocked:
            with open(full, "r+b") as f:
                f.seek(offset)
                f.write(data)
            return
        with open(full, "r+b") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            f.seek(16 + block_index * 16)
            ks = AES.new(self._require_dek(), AES.MODE_CTR, counter=_make_ctr(iv, block_index)).encrypt(
                b"\x00" * (block_offset + len(data))
            )
            ct = bytes(d ^ k for d, k in zip(data, ks[block_offset:]))
            f.seek(16 + offset)
            f.write(ct)

    def write_secret(self, path: str, data: bytes) -> None:
        if self._preunlocked:
            self.write_file("own", path, data)
            return
        iv = os.urandom(16)
        ct = AES.new(self._require_dek(), AES.MODE_CTR, counter=_make_ctr(iv)).encrypt(data)
        full = self._card_file("own", path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        tmp = full + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(iv + ct)
            os.replace(tmp, full)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def read_secret_stream(self, path: str, offset: int, length: int):
        if self._preunlocked:
            yield from self.read_file_stream("own", path, offset, length)
            return
        with open(self._card_file("own", path), "rb") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            f.seek(16 + block_index * 16)
            cipher = AES.new(self._require_dek(), AES.MODE_CTR, counter=_make_ctr(iv, block_index))
            skip = block_offset
            yielded = 0
            while yielded < length:
                ct = f.read(min(CHUNK, skip + length - yielded))
                if not ct:
                    break
                pt = cipher.decrypt(ct)
                if skip:
                    pt = pt[skip:]
                    skip = 0
                to_yield = min(len(pt), length - yielded)
                yield pt[:to_yield]
                yielded += to_yield

    def write_secret_stream(self, path: str) -> WriteHandle | EncryptedWriteHandle:
        if self._preunlocked:
            return self.write_file_stream("own", path)
        return EncryptedWriteHandle(self._card_file("own", path), self._require_dek())

    # -- QR scanner ------------------------------------------------------------

    def qr_ping(self) -> bool:
        return True  # scanner always present in sim

    def qr_poll(self):  # -> str | None
        if self._stopped:
            raise _PoweredOff()
        with self._qr_lock:
            val = self._pending_qr
            if val is not None:
                self._pending_qr = None
                return val
        return None

    def qr_scan(self):  # -> str | None
        import time as _t
        deadline = _t.monotonic() + 30
        while _t.monotonic() < deadline:
            if self._stopped:
                raise _PoweredOff()
            val = self.qr_poll()
            if val is not None:
                return val
            _t.sleep(0.1)
        return None

    # -- Battery ---------------------------------------------------------------

    def battery_status(self):  # -> tuple[int, bool, bool]  (pct, charging, vbus_good)
        return (self._battery_pct, self._charging, self._vbus)

    # -- Power management ------------------------------------------------------

    def power_button_pressed(self) -> bool:
        return False

    def power_off(self) -> None:
        raise _PoweredOff()

    def feed_watchdog(self) -> None:
        pass

    # -- Time ------------------------------------------------------------------

    def ticks_ms(self) -> int:
        import time
        return int(time.monotonic_ns() // 1_000_000) & 0x3FFFFFFF  # 30-bit wrap

    def rtc_now(self) -> int:
        import time
        return int(time.time())

    def notify_screen(self, name: str) -> None:
        if self._stopped:
            return
        socketio.emit("screen_nav", {"name": name}, room=self._device_id, namespace=self._namespace)

    def notify_qr(self, payload: str) -> None:
        if self._stopped:
            return
        socketio.emit("qr_displayed", {"payload": payload}, room=self._device_id, namespace=self._namespace)
