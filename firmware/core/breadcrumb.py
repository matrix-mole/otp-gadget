try:
    import gc as _gc
except ImportError:
    _gc = None

_RESET_CAUSE_NAMES = {
    1: "PWRON",
    2: "HARD_RESET",
    3: "WDT_RESET",
    4: "DEEPSLEEP",
    5: "SOFT_RESET",
}
_HISTORY_MAX = 4096
_RING_MAX = 4096
_TRAIL_MAX = 12

_trail = []


def _base_label(label: str) -> str:
    for sep in (".", "@"):
        i = label.find(sep)
        if i != -1:
            label = label[:i]
    return label


def recent_trail() -> list:
    return list(_trail)


def clear_trail() -> None:
    _trail.clear()


def mark(hal, label: str) -> None:
    try:
        ts = hal.rtc_now()
        ticks = hal.ticks_ms()
        try:
            free = _gc.mem_free() if _gc else 0
        except AttributeError:
            free = 0
        line = f"{ts} {ticks} {free} {label}\n".encode()
        hal.flash_write("last_state.txt", line)
        try:
            existing = hal.flash_read("breadcrumb_ring.txt")
        except OSError:
            existing = b""
        combined = existing + line
        if len(combined) > _RING_MAX:
            hal.flash_write("breadcrumb_ring.prev", existing)
            combined = line
        hal.flash_write("breadcrumb_ring.txt", combined)

        base = _base_label(label)
        if not _trail or _trail[-1] != base:
            if len(_trail) >= _TRAIL_MAX:
                _trail.pop(0)
            _trail.append(base)
    except Exception:
        pass


def boot_log(hal) -> None:
    try:
        ts = hal.rtc_now()
        try:
            import machine
            cause_int = machine.reset_cause()
            cause_name = _RESET_CAUSE_NAMES.get(cause_int, str(cause_int))
        except ImportError:
            cause_int = 0
            cause_name = "sim"
        line = f"{ts} {cause_int} {cause_name}\n".encode()
        try:
            existing = hal.flash_read("reboot_history.txt")
        except OSError:
            existing = b""
        combined = existing + line
        if len(combined) > _HISTORY_MAX:
            hal.flash_write("reboot_history.prev", existing)
            combined = line
        hal.flash_write("reboot_history.txt", combined)
    except Exception:
        pass
