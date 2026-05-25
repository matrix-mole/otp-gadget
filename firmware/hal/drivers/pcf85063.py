_PCF85063_ADDR   = 0x51
_REG_CTRL1       = 0x00
_REG_SECONDS     = 0x04
_EPOCH_YEAR      = 1970

_DAYS_PER_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _bcd(v):
    return (v // 16 * 10) + (v % 16)


def _to_bcd(v):
    return (v // 10 * 16) + (v % 10)


def _is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)


def _to_unix(year, month, day, hour, minute, second):
    days = 0
    for y in range(1970, year):
        days += 366 if _is_leap(y) else 365
    for m in range(1, month):
        d = _DAYS_PER_MONTH[m]
        if m == 2 and _is_leap(year):
            d = 29
        days += d
    days += day - 1
    return days * 86400 + hour * 3600 + minute * 60 + second


class PCF85063:
    def __init__(self, i2c):
        self._bus = i2c
        self._ensure_running()

    def _ensure_running(self):
        try:
            self._bus.writeto(_PCF85063_ADDR, bytes([_REG_CTRL1]))
            ctrl = self._bus.readfrom(_PCF85063_ADDR, 1)[0]
            if ctrl & 0x20:  # STOP bit set - RTC is halted
                self._bus.writeto_mem(_PCF85063_ADDR, _REG_CTRL1, bytes([ctrl & ~0x20]))
        except Exception:
            pass

    def _read_time(self):
        try:
            self._bus.writeto(_PCF85063_ADDR, bytes([_REG_SECONDS]))
            raw = self._bus.readfrom(_PCF85063_ADDR, 7)
        except Exception:
            return None
        second  = _bcd(raw[0] & 0x7F)
        minute  = _bcd(raw[1] & 0x7F)
        hour    = _bcd(raw[2] & 0x3F)
        day     = _bcd(raw[3] & 0x3F)
        # raw[4] is weekday - skip
        month   = _bcd(raw[5] & 0x1F)
        year    = _bcd(raw[6]) + _EPOCH_YEAR
        return year, month, day, hour, minute, second

    def read_unix(self) -> int:
        t = self._read_time()
        if t is None:
            return 0
        return _to_unix(*t)

    def set_time(self, year, month, day, hour, minute, second):
        payload = bytes([
            _to_bcd(second),
            _to_bcd(minute),
            _to_bcd(hour),
            _to_bcd(day),
            0,  # weekday - don't care
            _to_bcd(month),
            _to_bcd(year - _EPOCH_YEAR),
        ])
        try:
            self._bus.writeto_mem(_PCF85063_ADDR, _REG_SECONDS, payload)
        except Exception:
            pass
