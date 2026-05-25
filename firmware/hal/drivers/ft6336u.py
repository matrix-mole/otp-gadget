import machine
import time

FT6336U_ADDR              = 0x38
FT6336U_ADDR_CHIP_ID      = 0xA3
FT6336U_ADDR_GESTURE_EN   = 0xD0
FT6336U_ADDR_TD_STATUS    = 0x02
FT6336U_ADDR_TOUCH1_X     = 0x03

FT6336U_Point_Mode   = 0
FT6336U_Gesture_Mode = 1


class touch_ft6336u:
    """FT6336U capacitive touch driver - polled from the main thread.

    The chip's INT line on GPIO25 is wired but no interrupt handler is
    attached. Touch state is read on every call to get_touch_xy() from
    the UI loop, keeping all I2C traffic on a single thread so it can't
    collide with concurrent transactions to the AXP2101 / PCF85063 on
    the shared I2C1 bus. See firmware/README.md "Touch" section.
    """

    def __init__(self, device_addr=FT6336U_ADDR, mode=FT6336U_Point_Mode,
                 i2c_num=1, i2c_sda=34, i2c_scl=35, rst_pin=24, bus=None):

        if bus is not None:
            self.bus = bus
        else:
            self.bus = machine.I2C(id=i2c_num,
                                   scl=machine.Pin(i2c_scl),
                                   sda=machine.Pin(i2c_sda),
                                   freq=400_000)

        self.device_addr = device_addr
        self.mode = mode

        self.rst = machine.Pin(rst_pin, machine.Pin.OUT)

        self.reset()
        self._init_chip()

    def reset(self):
        self.rst(1)
        time.sleep_ms(200)
        self.rst(0)
        time.sleep_ms(200)
        self.rst(1)
        time.sleep_ms(200)

    def _write_reg(self, reg, value):
        try:
            self.bus.writeto(self.device_addr, bytes([reg, value]))
        except Exception:
            pass

    def _read_bytes(self, reg, length):
        try:
            self.bus.writeto(self.device_addr, bytes([reg]))
            return self.bus.readfrom(self.device_addr, length)
        except Exception:
            return None

    def _init_chip(self):
        if self.mode == FT6336U_Gesture_Mode:
            self._write_reg(FT6336U_ADDR_GESTURE_EN, 0x01)
        else:
            self._write_reg(FT6336U_ADDR_GESTURE_EN, 0x00)

        buf = self._read_bytes(FT6336U_ADDR_CHIP_ID, 1)
        chip_id = buf[0] if buf else 0
        if chip_id != 0x64:
            raise RuntimeError("FT6336U chip ID mismatch: got 0x{:02X}".format(chip_id))

    def get_touch_xy(self):
        """Return [{"x": ..., "y": ...}] in chip-native coords, or None.

        Polls TD_STATUS over I2C. On this board the chip reports portrait
        coordinates directly (x in 0..319, y in 0..479).
        """
        buf = self._read_bytes(FT6336U_ADDR_TD_STATUS, 1)
        if buf is None:
            return None
        if (buf[0] & 0x0F) == 0:
            return None
        xy = self._read_bytes(FT6336U_ADDR_TOUCH1_X, 4)
        if xy is None:
            return None
        x = ((xy[0] & 0x0F) << 8) | xy[1]
        y = ((xy[2] & 0x0F) << 8) | xy[3]
        return [{"x": x, "y": y}]
