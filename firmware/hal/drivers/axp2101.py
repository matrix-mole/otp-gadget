_AXP2101_ADDR      = 0x34
_REG_COMMON_CONFIG = 0x10
_REG_STATUS1       = 0x00
_REG_STATUS2       = 0x01
_REG_PWRON_STATUS  = 0x20   # Power-on source recording (read-only, clears on system reset)
_REG_INTEN2        = 0x41
_REG_INTSTS2       = 0x49
_REG_BAT_PERCENT   = 0xA4

# PWRON_STATUS (0x20) bit masks
_PWRON_BTN  = 0x01  # bit 0: PWRON key held for ≥ ONLEVEL caused power-on
_PWRON_VBUS = 0x04  # bit 2: VBUS insert-and-good caused power-on


class AXP2101:
    def __init__(self, i2c):
        self._bus = i2c

    def _read(self, reg):
        try:
            self._bus.writeto(_AXP2101_ADDR, bytes([reg]))
            return self._bus.readfrom(_AXP2101_ADDR, 1)[0]
        except Exception:
            return 0

    def _write(self, reg, val) -> None:
        try:
            self._bus.writeto(_AXP2101_ADDR, bytes([reg, val]))
        except Exception:
            pass

    def pwron_was_vbus(self) -> bool:
        """Return True if VBUS insertion (not the power button) triggered this boot.

        The AXP2101 records the power-on source in REG20H on every boot.
        Bit 2 (vbus_pwron_stat) is set when VBUS insert-and-good caused the
        power-on; bit 0 (btn_pwron_stat) is set when the POK button did.
        Reading both lets the firmware distinguish "plugged in to charge"
        (VBUS only) from "user pressed button while charging" (button set,
        VBUS may or may not also be set).

        NOTE: needs hardware verification - see firmware/README.md bring-up
        checklist item 13.
        """
        sts = self._read(_REG_PWRON_STATUS)
        return bool(sts & _PWRON_VBUS) and not bool(sts & _PWRON_BTN)

    def enable_pkey_irq(self) -> None:
        self._write(_REG_INTEN2, self._read(_REG_INTEN2) | 0x08)

    def pkey_short_pressed(self) -> bool:
        sts = self._read(_REG_INTSTS2)
        if sts & 0x08:
            self._write(_REG_INTSTS2, 0x08)  # W1C
            return True
        return False

    def power_off(self) -> None:
        self._write(_REG_COMMON_CONFIG, self._read(_REG_COMMON_CONFIG) | 0x01)

    def battery_percent(self) -> int:
        return min(100, self._read(_REG_BAT_PERCENT))

    def is_charging(self) -> bool:
        # bits [7:5] of STATUS2: 001 = charging
        return (self._read(_REG_STATUS2) >> 5) == 0x01

    def vbus_good(self) -> bool:
        """Return True if USB-C is plugged in and supplying valid voltage (VBUS good).

        STATUS1 (0x00) bit 5 = VBUS_GD.  True whenever the cable is present,
        regardless of whether the battery is actively charging or already full.
        This is the right signal for distinguishing "on USB" from "on battery".

        NOTE: needs hardware verification - see firmware/README.md bring-up
        checklist (same caveat as pwron_was_vbus).
        """
        return bool(self._read(_REG_STATUS1) & 0x20)
