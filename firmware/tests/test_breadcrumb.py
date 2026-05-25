import tempfile
import os
import time

from firmware.core import breadcrumb


class _FakeHal:
    def __init__(self, flash_dir: str):
        self._flash_dir = flash_dir

    def _path(self, name: str) -> str:
        return os.path.join(self._flash_dir, name.lstrip("/"))

    def flash_write(self, path: str, data: bytes) -> None:
        full = self._path(path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def flash_read(self, path: str) -> bytes:
        with open(self._path(path), "rb") as f:
            return f.read()

    def flash_exists(self, path: str) -> bool:
        return os.path.exists(self._path(path))

    def ticks_ms(self) -> int:
        return int(time.monotonic() * 1000) & 0x3FFFFFFF

    def rtc_now(self) -> int:
        return int(time.time())


def _hal():
    td = tempfile.mkdtemp()
    return _FakeHal(td), td


def test_mark_writes_last_state():
    hal, td = _hal()
    breadcrumb.mark(hal, "Home")
    data = hal.flash_read("last_state.txt").decode()
    parts = data.strip().split(" ", 3)
    assert len(parts) == 4
    assert parts[3] == "Home"
    assert int(parts[0]) > 0  # timestamp


def test_mark_overwrites_previous():
    hal, td = _hal()
    breadcrumb.mark(hal, "Home")
    breadcrumb.mark(hal, "Send")
    data = hal.flash_read("last_state.txt").decode()
    assert "Send" in data
    assert "Home" not in data


def test_mark_does_not_raise_on_write_error():
    class _BadHal(_FakeHal):
        def flash_write(self, path, data):
            raise OSError("disk full")
    hal = _BadHal("/nonexistent")
    breadcrumb.mark(hal, "Home")  # must not raise


def test_boot_log_appends():
    hal, td = _hal()
    breadcrumb.boot_log(hal)
    breadcrumb.boot_log(hal)
    data = hal.flash_read("reboot_history.txt").decode()
    lines = [l for l in data.strip().splitlines() if l]
    assert len(lines) == 2
    for line in lines:
        parts = line.split()
        assert len(parts) == 3
        assert parts[2] == "sim"


def test_boot_log_rotates_at_4kb():
    hal, td = _hal()
    # Fill history over the 4096-byte limit ("0 0 sim\n" is 9 bytes; 512 * 9 = 4608 bytes)
    big_line = ("0 0 sim\n" * 512).encode()
    hal.flash_write("reboot_history.txt", big_line)
    breadcrumb.boot_log(hal)
    # After rotation, only the new single line remains
    data = hal.flash_read("reboot_history.txt").decode()
    lines = [l for l in data.strip().splitlines() if l]
    assert len(lines) == 1
    # The old data was moved to .prev
    assert hal.flash_exists("reboot_history.prev")


# --- In-RAM trail ---

def test_trail_records_base_screen_name():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    breadcrumb.mark(hal, "Home")
    assert breadcrumb.recent_trail() == ["Home"]


def test_trail_strips_phase_and_progress():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    breadcrumb.mark(hal, "FinalizeExchange.b_generate_otp@4194304/10485760")
    assert breadcrumb.recent_trail() == ["FinalizeExchange"]


def test_trail_deduplicates_consecutive():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    breadcrumb.mark(hal, "Home")
    breadcrumb.mark(hal, "Home")
    assert breadcrumb.recent_trail() == ["Home"]


def test_trail_records_sequence():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    for label in ("PINEntry", "Home", "Send", "ShowCiphertext"):
        breadcrumb.mark(hal, label)
    assert breadcrumb.recent_trail() == ["PINEntry", "Home", "Send", "ShowCiphertext"]


def test_trail_caps_at_max():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    for i in range(20):
        breadcrumb.mark(hal, f"Screen{i}")
    trail = breadcrumb.recent_trail()
    assert len(trail) <= 12
    assert trail[-1] == "Screen19"


def test_clear_trail():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    breadcrumb.mark(hal, "Home")
    breadcrumb.clear_trail()
    assert breadcrumb.recent_trail() == []


def test_recent_trail_returns_copy():
    breadcrumb.clear_trail()
    hal, _ = _hal()
    breadcrumb.mark(hal, "Home")
    t = breadcrumb.recent_trail()
    t.append("Injected")
    assert "Injected" not in breadcrumb.recent_trail()
