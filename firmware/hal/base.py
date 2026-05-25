class HALBase:
    # Entropy
    def get_random_bytes(self, n: int) -> bytes:
        raise NotImplementedError

    # Display - portrait 320×480
    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        raise NotImplementedError

    def blit_rect(self, x: int, y: int, w: int, h: int, buf: bytes) -> None:
        raise NotImplementedError

    # Touch - portrait coords, origin top-left, or None
    def get_touch(self):  # -> tuple[int, int] | None
        raise NotImplementedError

    def inject_touch(self, x: int, y: int) -> None:
        pass

    # SD cards
    def mount_card(self, slot: str) -> str:  # 'MOUNTED'|'NO_CARD'|'WRONG_FS'
        raise NotImplementedError

    def unmount_card(self, slot: str) -> None:
        raise NotImplementedError

    def read_file(self, slot: str, path: str) -> bytes:
        raise NotImplementedError

    def write_file(self, slot: str, path: str, data: bytes) -> None:
        raise NotImplementedError

    def delete_file(self, slot: str, path: str) -> None:
        raise NotImplementedError

    def delete_tree(self, slot: str, path: str) -> None:
        raise NotImplementedError

    def file_exists(self, slot: str, path: str) -> bool:
        raise NotImplementedError

    def free_space(self, slot: str) -> int:
        raise NotImplementedError

    # Plaintext streaming (multi-MB files that don't fit in RAM)
    def read_file_stream(self, slot: str, path: str, offset: int, length: int):  # -> Iterator[bytes]
        raise NotImplementedError

    def write_file_stream(self, slot: str, path: str):  # -> WriteHandle
        raise NotImplementedError

    # Secrets key management
    def unlock_secrets(self, key: bytes) -> None:
        raise NotImplementedError

    def lock_secrets(self) -> None:
        raise NotImplementedError

    # Encrypted own-card secrets - requires unlock_secrets() first
    def read_secret(self, path: str) -> bytes:
        raise NotImplementedError

    def read_secret_slice(self, path: str, offset: int, length: int) -> bytes:
        raise NotImplementedError

    def overwrite_secret_slice(self, path: str, offset: int, data: bytes) -> None:
        raise NotImplementedError

    def write_secret(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    # Encrypted streaming for pad files
    def read_secret_stream(self, path: str, offset: int, length: int):  # -> Iterator[bytes]
        raise NotImplementedError

    def write_secret_stream(self, path: str):  # -> WriteHandle
        raise NotImplementedError

    # MCU flash (device_secret, PIN attempt state)
    def flash_read(self, path: str) -> bytes:
        raise NotImplementedError

    def flash_write(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def flash_exists(self, path: str) -> bool:
        raise NotImplementedError

    def flash_delete(self, path: str) -> None:
        raise NotImplementedError

    # QR scanner
    def qr_ping(self) -> bool:
        raise NotImplementedError

    def qr_scan(self):  # -> str | None
        raise NotImplementedError

    def qr_poll(self):  # -> str | None  (non-blocking single check)
        raise NotImplementedError

    # Battery
    def battery_status(self):  # -> tuple[int, bool, bool]  (pct, charging, vbus_good)
        raise NotImplementedError

    # Power management
    def power_button_pressed(self) -> bool:
        raise NotImplementedError

    def power_off(self) -> None:
        raise NotImplementedError

    def feed_watchdog(self) -> None:
        raise NotImplementedError

    # Monotonic millisecond counter (wraps per MicroPython semantics)
    def ticks_ms(self) -> int:
        raise NotImplementedError

    # Wall-clock unix seconds from onboard RTC (survives power cycles)
    def rtc_now(self) -> int:
        raise NotImplementedError

    # Sim-only: notify which screen is active (no-op on real hardware)
    def notify_screen(self, name: str) -> None:
        pass

    # Sim-only: notify that a QR payload is currently rendered on screen (no-op on real hardware)
    def notify_qr(self, payload: str) -> None:
        pass
