import os
import time

from machine import I2C, Pin, SPI, SoftSPI, UART, WDT

from firmware.hal.base import HALBase
from firmware.hal.drivers.axp2101 import AXP2101
from firmware.hal.drivers.ft6336u import touch_ft6336u
from firmware.hal.drivers.pcf85063 import PCF85063
from firmware.hal.drivers.st7789 import lcd_st7789
from firmware.hal.drivers import sdcard
from firmware.core.crypto.ctr import CTRStream, aes_ctr_xor

# RP2350 does not have os.urandom in all builds - detect once at import time.
try:
    os.urandom(1)
    _urandom = os.urandom
except (AttributeError, OSError):
    import random as _random

    def _urandom(n: int) -> bytes:
        buf = bytearray(n)
        i = 0
        while i + 4 <= n:
            v = _random.getrandbits(32)
            buf[i] = v & 0xFF
            buf[i + 1] = (v >> 8) & 0xFF
            buf[i + 2] = (v >> 16) & 0xFF
            buf[i + 3] = v >> 24
            i += 4
        while i < n:
            buf[i] = _random.getrandbits(8)
            i += 1
        return bytes(buf)


CHUNK = 4096

_FIRST_BOOT_UNIX = 1735689600  # 2026-01-01 00:00:00 UTC - RTC baseline


def _makedirs(path: str) -> None:
    """Create parent directories for path on FAT32 (ignores EEXIST)."""
    parts = path.split("/")
    cur = ""
    for p in parts[:-1]:
        if not p:
            continue
        cur = cur + "/" + p if cur else p
        try:
            os.mkdir(cur)
        except OSError:
            pass


class WriteHandle:
    def __init__(self, final_path: str) -> None:
        self._final = final_path
        self._tmp = final_path + ".tmp"
        _makedirs(self._tmp)
        self._f = open(self._tmp, "wb")

    def write(self, data: bytes) -> None:
        self._f.write(data)

    def seek(self, offset: int) -> None:
        self._f.seek(offset)

    def close(self) -> None:
        self._f.close()
        os.rename(self._tmp, self._final)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._f.close()
        except OSError:
            pass
        if exc_type is None:
            os.rename(self._tmp, self._final)
        else:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
        return False


class EncryptedWriteHandle:
    def __init__(self, final_path: str, key: bytes) -> None:
        self._final = final_path
        self._tmp = final_path + ".tmp"
        _makedirs(self._tmp)
        iv = _urandom(16)
        self._stream = CTRStream(key, iv)
        self._f = open(self._tmp, "wb")
        self._f.write(iv)

    def write(self, data: bytes) -> None:
        self._f.write(self._stream.update(data))

    def close(self) -> None:
        self._f.close()
        os.rename(self._tmp, self._final)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._f.close()
        except OSError:
            pass
        if exc_type is None:
            os.rename(self._tmp, self._final)
        else:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
        return False


class RealHAL(HALBase):

    _QR_HEARTBEAT_TX = b"\x7E\x00\x0A\x01\x00\x00\x00\x30\x1A"
    _QR_HEARTBEAT_RX = b"\x03\x00\x00\x01\x00\x33\x31"
    _QR_TRIGGER = b"\x7E\x00\x08\x01\x00\x02\x01\xAB\xCD"
    _QR_TRIGGER_ACK = b"\x02\x00\x00\x01\x00\x33\x31"
    _QR_SAVE_FLASH = b"\x7E\x00\x09\x01\x00\x00\x00\xDE\xC8"
    _QR_WRITE_ACK = b"\x02\x00\x00\x01\x00\x33\x31"

    def __init__(self) -> None:
        # Shared I2C1 bus for touch, battery, and RTC (all on GPIO34/35).
        # AXP2101 is initialized first so we can check the power-on source
        # before spending time on the display or other peripherals.
        self._i2c = I2C(1, scl=Pin(35), sda=Pin(34), freq=400_000)

        self._axp = AXP2101(self._i2c)
        self._axp.enable_pkey_irq()
        self._axp.pkey_short_pressed()  # drain any stale pre-boot press

        # Suppress VBUS-triggered auto-boot: if plugging in a USB-C cable
        # (not a button press) caused this power-on, power off immediately.
        # The AXP2101 continues charging the battery; only the MCU shuts down.
        # Users who want to use the device while charging press the button.
        # See firmware/README.md bring-up checklist item 13 for verification.
        if self._axp.pwron_was_vbus():
            self._axp.power_off()
            # Never reached; power_off() cuts MCU power via AXP2101.

        # Watchdog: enabled when /flash/watchdog.txt exists (toggle via inspect.sh watchdog-on/off).
        if self.flash_exists("watchdog.txt"):
            self._wdt = WDT(timeout=8000)
        else:
            self._wdt = None

        # Debug mode: file-based touch injection when /flash/debug_mode.txt exists.
        self._debug_mode = self.flash_exists("debug_mode.txt")

        self._lcd = lcd_st7789()
        self._touch = touch_ft6336u(bus=self._i2c)
        self._rtc = PCF85063(self._i2c)

        # Seed RTC on first boot (unix seconds == 0 means chip was reset)
        if self._rtc.read_unix() < _FIRST_BOOT_UNIX:
            self._rtc.set_time(2026, 1, 1, 0, 0, 0)

        # Own SD: SPI1 on GPIO26/27/28, CS on GPIO31
        self._own_spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0,
                            bits=8, firstbit=SPI.MSB,
                            sck=Pin(26), mosi=Pin(27), miso=Pin(28))
        self._own_cs = Pin(31, Pin.OUT, value=1)
        self._own_mounted = False

        # Guest SD: SoftSPI on GPIO3 (MOSI) / GPIO32 (MISO) / GPIO6 (SCK), CS on GPIO33.
        # Must NOT use hardware SPI(0) here - the LCD also uses SPI0 (GPIO18/19).
        # SDCard.init_card() calls spi.init(baudrate=100000) which would slow the LCD
        # to 100 kHz (a full-screen fill takes ~24 s instead of ~16 ms).
        self._guest_spi = SoftSPI(baudrate=5_000_000, polarity=0, phase=0,
                                  bits=8, firstbit=SoftSPI.MSB,
                                  sck=Pin(6), mosi=Pin(3), miso=Pin(32))
        self._guest_cs = Pin(33, Pin.OUT, value=1)
        self._guest_mounted = False

        # QR scanner: UART1 on GPIO4 (TX) / GPIO5 (RX). Factory-default 9600 8N1.
        # Read mode + tail are reconfigured at runtime by _qr_ensure_config.
        self._qr_uart = UART(1, baudrate=9600, tx=Pin(4), rx=Pin(5),
                             bits=8, parity=None, stop=1, timeout=200)
        self._qr_configured = False

        # qr_poll state
        self._qr_poll_active = False
        self._qr_poll_buf = b""
        self._qr_poll_last = 0

        # In-RAM DEK
        self._dek: bytearray | None = None

    # -- Display ---------------------------------------------------------------

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        self._lcd.fill_rect(x, y, w, h, color)

    def blit_rect(self, x: int, y: int, w: int, h: int, buf: bytes) -> None:
        self._lcd.blit_rect(x, y, w, h, buf)

    # -- Touch -----------------------------------------------------------------

    def inject_touch(self, x: int, y: int) -> None:
        """Debug-only: append (x, y) to the file-based injection queue."""
        if not self._debug_mode:
            return
        try:
            try:
                existing = self.flash_read("inject_queue.txt").decode()
            except OSError:
                existing = ""
            self.flash_write("inject_queue.txt", (existing + f"{x},{y}\n").encode())
        except Exception:
            pass

    def get_touch(self):
        """Return portrait (x, y) or None.

        The FT6336U on this board reports portrait coordinates directly
        (x in 0..319, y in 0..479) - no rotation needed. Verified with the
        five-target sweep in check_13_touch_alignment.py; see bring-up log
        item 8b.

        When debug_mode is on, drains the file-based injection queue first.
        """
        if self._debug_mode:
            try:
                raw = self.flash_read("inject_queue.txt").decode()
                lines = [l for l in raw.splitlines() if l.strip()]
                if lines:
                    first, rest = lines[0], lines[1:]
                    if rest:
                        self.flash_write("inject_queue.txt", "\n".join(rest).encode() + b"\n")
                    else:
                        self.flash_delete("inject_queue.txt")
                    x_str, y_str = first.split(",")
                    return (int(x_str), int(y_str))
            except Exception:
                pass
        pt = self._touch.get_touch_xy()
        if pt is None:
            return None
        return (pt[0]["x"], pt[0]["y"])

    # -- Entropy ---------------------------------------------------------------

    def get_random_bytes(self, n: int) -> bytes:
        return _urandom(n)

    # -- MCU flash -------------------------------------------------------------

    def flash_read(self, path: str) -> bytes:
        with open("/flash/" + path.lstrip("/"), "rb") as f:
            return f.read()

    def flash_write(self, path: str, data: bytes) -> None:
        full = "/flash/" + path.lstrip("/")
        _makedirs(full)
        tmp = full + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.rename(tmp, full)

    def flash_exists(self, path: str) -> bool:
        try:
            os.stat("/flash/" + path.lstrip("/"))
            return True
        except OSError:
            return False

    def flash_delete(self, path: str) -> None:
        try:
            os.remove("/flash/" + path.lstrip("/"))
        except OSError:
            pass

    # -- SD cards --------------------------------------------------------------

    def mount_card(self, slot: str) -> str:
        if slot == "own":
            if self._own_mounted:
                return "MOUNTED"
            try:
                sd = sdcard.SDCard(self._own_spi, self._own_cs, baudrate=5_000_000)
                os.mount(sd, "/sd")
                self._own_mounted = True
                self._sweep_stale_tmp()
                return "MOUNTED"
            except Exception:
                return "NO_CARD"

        if slot == "guest":
            if self._guest_mounted:
                return "MOUNTED"
            try:
                sd = sdcard.SDCard(self._guest_spi, self._guest_cs, baudrate=5_000_000)
                os.mount(sd, "/sd2")
                self._guest_mounted = True
                return "MOUNTED"
            except Exception:
                return "NO_CARD"

        raise ValueError("Unknown slot: " + repr(slot))

    def _sweep_stale_tmp(self) -> None:
        """Remove any *.tmp files left behind by writes that didn't reach rename.

        Hard resets mid-write (watchdog, battery yank, PWR long-press) skip the
        write handle's __exit__, so the .tmp survives into the next boot. Sweep
        on mount keeps FAT clean and prevents orphan accumulation.
        """
        for d in ("/sd/secret", "/sd/exchange"):
            self._sweep_dir(d)
        try:
            for entry in os.listdir("/sd/secret/contacts"):
                self._sweep_dir("/sd/secret/contacts/" + entry)
        except OSError:
            pass  # /secret/contacts doesn't exist yet (no contacts committed)

    def _sweep_dir(self, path: str) -> None:
        try:
            entries = os.listdir(path)
        except OSError:
            return
        for entry in entries:
            if entry.endswith(".tmp"):
                try:
                    os.remove(path + "/" + entry)
                except OSError:
                    pass

    def unmount_card(self, slot: str) -> None:
        if slot == "own" and self._own_mounted:
            try:
                os.umount("/sd")
            except OSError:
                pass
            self._own_mounted = False
        elif slot == "guest" and self._guest_mounted:
            try:
                os.umount("/sd2")
            except OSError:
                pass
            self._guest_mounted = False

    def _card_path(self, slot: str, path: str) -> str:
        if slot == "own":
            return "/sd/" + path.lstrip("/")
        if slot == "guest":
            return "/sd2/" + path.lstrip("/")
        raise ValueError("Unknown slot: " + repr(slot))

    def read_file(self, slot: str, path: str) -> bytes:
        with open(self._card_path(slot, path), "rb") as f:
            return f.read()

    def write_file(self, slot: str, path: str, data: bytes) -> None:
        full = self._card_path(slot, path)
        _makedirs(full)
        tmp = full + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.rename(tmp, full)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def delete_file(self, slot: str, path: str) -> None:
        os.remove(self._card_path(slot, path))

    def delete_tree(self, slot: str, path: str) -> None:
        self._rmtree(self._card_path(slot, path))

    def _rmtree(self, path: str) -> None:
        try:
            st = os.stat(path)
        except OSError:
            return
        if st[0] & 0x4000:  # S_IFDIR
            for entry in os.listdir(path):
                self._rmtree(path + "/" + entry)
            try:
                os.rmdir(path)
            except OSError:
                pass
        else:
            try:
                os.remove(path)
            except OSError:
                pass

    def file_exists(self, slot: str, path: str) -> bool:
        try:
            os.stat(self._card_path(slot, path))
            return True
        except OSError:
            return False

    def free_space(self, slot: str) -> int:
        s = os.statvfs("/sd" if slot == "own" else "/sd2")
        return s[0] * s[3]

    # -- Plaintext streaming ---------------------------------------------------

    def read_file_stream(self, slot: str, path: str, offset: int, length: int):
        with open(self._card_path(slot, path), "rb") as f:
            f.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)

    def write_file_stream(self, slot: str, path: str) -> WriteHandle:
        return WriteHandle(self._card_path(slot, path))

    # -- Secrets ---------------------------------------------------------------

    def _require_dek(self) -> bytes:
        if self._dek is None:
            raise RuntimeError("Secrets not unlocked")
        return bytes(self._dek)

    def unlock_secrets(self, key: bytes) -> None:
        self._dek = bytearray(key)

    def lock_secrets(self) -> None:
        if self._dek is not None:
            for i in range(len(self._dek)):
                self._dek[i] = 0
        self._dek = None

    def _secret_path(self, path: str) -> str:
        return "/sd/" + path.lstrip("/")

    def read_secret(self, path: str) -> bytes:
        with open(self._secret_path(path), "rb") as f:
            iv = f.read(16)
            ct = f.read()
        return aes_ctr_xor(self._require_dek(), iv, ct)

    def read_secret_slice(self, path: str, offset: int, length: int) -> bytes:
        dek = self._require_dek()
        with open(self._secret_path(path), "rb") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            f.seek(16 + block_index * 16)
            ct = f.read(block_offset + length)
        pt = aes_ctr_xor(dek, iv, ct, block_index=block_index)
        return pt[block_offset: block_offset + length]

    def overwrite_secret_slice(self, path: str, offset: int, data: bytes) -> None:
        dek = self._require_dek()
        with open(self._secret_path(path), "r+b") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            padded = (b"\x00" * block_offset) + data
            ct = aes_ctr_xor(dek, iv, padded, block_index=block_index)[block_offset:]
            f.seek(16 + offset)
            f.write(ct)

    def write_secret(self, path: str, data: bytes) -> None:
        iv = _urandom(16)
        ct = aes_ctr_xor(self._require_dek(), iv, data)
        full = self._secret_path(path)
        _makedirs(full)
        tmp = full + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(iv + ct)
            os.rename(tmp, full)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def read_secret_stream(self, path: str, offset: int, length: int):
        dek = self._require_dek()
        with open(self._secret_path(path), "rb") as f:
            iv = f.read(16)
            block_index = offset // 16
            block_offset = offset % 16
            f.seek(16 + block_index * 16)
            stream = CTRStream(dek, iv, start_block_index=block_index)
            skip = block_offset
            yielded = 0
            while yielded < length:
                ct = f.read(min(CHUNK, skip + length - yielded))
                if not ct:
                    break
                pt = stream.update(ct)
                if skip:
                    pt = pt[skip:]
                    skip = 0
                to_yield = min(len(pt), length - yielded)
                yield pt[:to_yield]
                yielded += to_yield

    def write_secret_stream(self, path: str) -> EncryptedWriteHandle:
        return EncryptedWriteHandle(self._secret_path(path), self._require_dek())

    # -- QR scanner ------------------------------------------------------------

    @staticmethod
    def _qr_crc(payload: bytes) -> bytes:
        # CRC-CCITT per GM861XS manual §10.1: poly 0x1021, init 0, no
        # reflection, no xor-out. Computed over Types+Lens+Address+Datas.
        crc = 0
        for b in payload:
            crc ^= b << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return bytes((crc >> 8, crc & 0xFF))

    def _qr_txn(self, frame: bytes, expect_len: int, timeout_ms: int = 500) -> bytes:
        self._qr_uart.read()
        self._qr_uart.write(frame)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        buf = b""
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            chunk = self._qr_uart.read(expect_len - len(buf))
            if chunk:
                buf += chunk
                if len(buf) >= expect_len:
                    return buf[:expect_len]
            time.sleep_ms(10)
        raise OSError("QR scanner: short response, got {} bytes ({})".format(
            len(buf), buf.hex() if buf else ""))

    def _qr_read_zone(self, addr: int) -> int:
        body = bytes((0x07, 0x01, (addr >> 8) & 0xFF, addr & 0xFF, 0x01))
        frame = b"\x7E\x00" + body + self._qr_crc(body)
        rsp = self._qr_txn(frame, expect_len=7)
        if rsp[:4] != b"\x02\x00\x00\x01":
            raise OSError("QR read: bad header " + rsp.hex())
        # Scanner CRCs responses over Lens+Address+Data (rsp[1:5]), excluding
        # the leading Type byte. Empirically verified against the hardware.
        if self._qr_crc(rsp[1:5]) != rsp[5:7]:
            raise OSError("QR read: bad CRC " + rsp.hex())
        return rsp[4]

    def _qr_write_zone(self, addr: int, value: int) -> None:
        body = bytes((0x08, 0x01, (addr >> 8) & 0xFF, addr & 0xFF, value & 0xFF))
        frame = b"\x7E\x00" + body + self._qr_crc(body)
        rsp = self._qr_txn(frame, expect_len=7)
        if rsp != self._QR_WRITE_ACK:
            raise OSError("QR write: bad ACK " + rsp.hex())

    def _qr_save_flash(self) -> None:
        rsp = self._qr_txn(self._QR_SAVE_FLASH, expect_len=7)
        if rsp != self._QR_WRITE_ACK:
            raise OSError("QR save: bad ACK " + rsp.hex())

    def _qr_ensure_config(self) -> None:
        if self._qr_configured:
            return
        needs_save = False
        # Zone 0x0000 bits 1-0 = 01 (Command Triggered); preserve LED/mute/lighting.
        cur = self._qr_read_zone(0x0000)
        target = (cur & 0xFC) | 0x01
        if cur != target:
            self._qr_write_zone(0x0000, target)
            needs_save = True
        # Zone 0x000D bits 1-0 = 00 (serial port output). Factory default is
        # USB HID-KBW, which silently drops decoded data on a UART-only wiring.
        cur = self._qr_read_zone(0x000D)
        target = cur & 0xFC
        if cur != target:
            self._qr_write_zone(0x000D, target)
            needs_save = True
        # Zone 0x0060 bits 6-5 = 01 (CRLF), bit 0 = 1 (allow tail); preserve rest.
        cur = self._qr_read_zone(0x0060)
        target = (cur & 0x9E) | 0x21
        if cur != target:
            self._qr_write_zone(0x0060, target)
            needs_save = True
        if needs_save:
            self._qr_save_flash()
        self._qr_configured = True

    def qr_ping(self) -> bool:
        self._qr_uart.read()
        self._qr_uart.write(self._QR_HEARTBEAT_TX)
        deadline = time.ticks_add(time.ticks_ms(), 500)
        buf = b""
        alive = False
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            chunk = self._qr_uart.read(16)
            if chunk:
                buf += chunk
                if self._QR_HEARTBEAT_RX in buf:
                    alive = True
                    break
            time.sleep_ms(20)
        if alive and not self._qr_configured:
            try:
                self._qr_ensure_config()
            except OSError as e:
                print("QR auto-config failed:", e)
                return False
        return alive

    def qr_scan(self):
        try:
            self._qr_ensure_config()
        except OSError as e:
            print("QR auto-config failed:", e)
            return None
        self._qr_uart.read()
        self._qr_uart.write(self._QR_TRIGGER)
        deadline = time.ticks_add(time.ticks_ms(), 30_000)
        buf = b""
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self._wdt is not None:
                self._wdt.feed()
            chunk = self._qr_uart.read(64)
            if chunk:
                buf += chunk
                i = buf.find(b"\r\n")
                if i != -1:
                    payload = buf[:i]
                    if payload.startswith(self._QR_TRIGGER_ACK):
                        payload = payload[len(self._QR_TRIGGER_ACK):]
                    try:
                        return payload.decode("ascii")
                    except UnicodeError:
                        return None
            time.sleep_ms(20)
        return None

    def qr_poll(self):
        now = time.ticks_ms()
        # >1 s without a poll call ⇒ caller left the scan screen; reset.
        if self._qr_poll_active and time.ticks_diff(now, self._qr_poll_last) > 1000:
            self._qr_poll_active = False
        self._qr_poll_last = now

        if not self._qr_poll_active:
            try:
                self._qr_ensure_config()
            except OSError as e:
                print("QR auto-config failed:", e)
                return None
            self._qr_uart.read()
            self._qr_uart.write(self._QR_TRIGGER)
            self._qr_poll_buf = b""
            self._qr_poll_active = True

        chunk = self._qr_uart.read(256)
        if chunk:
            self._qr_poll_buf += chunk

        i = self._qr_poll_buf.find(b"\r\n")
        if i == -1:
            return None

        payload = self._qr_poll_buf[:i]
        self._qr_poll_buf = b""
        self._qr_poll_active = False
        if payload.startswith(self._QR_TRIGGER_ACK):
            payload = payload[len(self._QR_TRIGGER_ACK):]
        try:
            return payload.decode("ascii")
        except UnicodeError:
            return None

    # -- Battery ---------------------------------------------------------------

    def battery_status(self):
        return (self._axp.battery_percent(), self._axp.is_charging(), self._axp.vbus_good())

    # -- Power management ------------------------------------------------------

    def power_button_pressed(self) -> bool:
        return self._axp.pkey_short_pressed()

    def power_off(self) -> None:
        self.lock_secrets()
        self._axp.power_off()

    def feed_watchdog(self) -> None:
        if self._wdt is not None:
            self._wdt.feed()

    # -- Time ------------------------------------------------------------------

    def ticks_ms(self) -> int:
        return time.ticks_ms() & 0x3FFFFFFF

    def rtc_now(self) -> int:
        return self._rtc.read_unix()
