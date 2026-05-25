from machine import Pin, SPI
import time

LCD_WIDTH  = 320
LCD_HEIGHT = 480

LCD_DC  = 20
LCD_CS  = 21
SCK     = 18
MOSI    = 19
MISO    = None
LCD_RST = 23
LCD_BL  = 22

class lcd_st7789:
    def __init__(self):
        self.width = LCD_WIDTH
        self.height = LCD_HEIGHT

        self.cs  = Pin(LCD_CS,  Pin.OUT)
        self.rst = Pin(LCD_RST, Pin.OUT)
        self.bl  = Pin(LCD_BL,  Pin.OUT)

        # Backlight stays OFF until the panel is initialized and the screen
        # has been cleared to a solid gray - otherwise the user sees the
        # ST7789's uninitialized internal RAM (random-pixel static) on
        # power-up. Gray (not black) makes it obvious the device is on
        # during the ~1-2 s before the first screen is drawn.
        self.bl(0)
        self.cs(1)

        self.bus = SPI(
            0,
            baudrate=230_000_000,
            polarity=0,
            phase=0,
            sck=Pin(SCK),
            mosi=Pin(MOSI),
            miso=MISO
        )
        self.dc = Pin(LCD_DC, Pin.OUT)
        self.dc(1)

        self.lcd_init()
        self.lcd_fill(0x4208)  # dark gray (RGB565) - visibly "on" before first draw
        self.bl(1)

    def write_cmd(self, cmd):
        self.dc(0)
        self.cs(0)
        self.bus.write(bytearray([cmd]))
        self.cs(1)

    def write_data(self, data):
        self.dc(1)
        self.cs(0)
        self.bus.write(bytearray([data]))
        self.cs(1)

    def lcd_init(self):
        self.rst(0)
        time.sleep_ms(100)
        self.rst(1)
        time.sleep_ms(100)

        self.write_cmd(0x11)
        time.sleep_ms(120)

        # MADCTL: 0x48 = MX (mirror columns) + BGR. Upstream Waveshare uses
        # 0x08 (BGR only); on this panel that produces a horizontally mirrored
        # image. See firmware/setup/rp2350-bringup-log.md item 8b.
        self.write_cmd(0x36)
        self.write_data(0x48)

        self.write_cmd(0x3A)
        self.write_data(0x05)

        self.write_cmd(0xF0)
        self.write_data(0xC3)
        self.write_cmd(0xF0)
        self.write_data(0x96)

        self.write_cmd(0xB4)
        self.write_data(0x01)

        self.write_cmd(0xB7)
        self.write_data(0xC6)

        self.write_cmd(0xC0)
        self.write_data(0x80)
        self.write_data(0x45)

        self.write_cmd(0xC1)
        self.write_data(0x13)

        self.write_cmd(0xC2)
        self.write_data(0xA7)

        self.write_cmd(0xC5)
        self.write_data(0x0A)

        self.write_cmd(0xE8)
        for d in [0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33]:
            self.write_data(d)

        self.write_cmd(0xE0)
        for d in [0xD0, 0x08, 0x0F, 0x06, 0x06, 0x33, 0x30, 0x33, 0x47, 0x17, 0x13, 0x13, 0x2B, 0x31]:
            self.write_data(d)

        self.write_cmd(0xE1)
        for d in [0xD0, 0x0A, 0x11, 0x0B, 0x09, 0x07, 0x2F, 0x33, 0x47, 0x38, 0x15, 0x16, 0x2C, 0x32]:
            self.write_data(d)

        self.write_cmd(0xF0)
        self.write_data(0x3C)
        self.write_cmd(0xF0)
        self.write_data(0x69)

        time.sleep_ms(120)

        self.write_cmd(0x21)
        self.write_cmd(0x29)

    def set_windows(self, Xstart, Ystart, Xend, Yend):
        self.write_cmd(0x2A)
        for v in [Xstart >> 8, Xstart & 0xFF, Xend >> 8, Xend & 0xFF]:
            self.write_data(v)
        self.write_cmd(0x2B)
        for v in [Ystart >> 8, Ystart & 0xFF, Yend >> 8, Yend & 0xFF]:
            self.write_data(v)
        self.write_cmd(0x2C)

    def draw_point(self, x, y, color):
        self.set_windows(x, y, x, y)
        self.dc(1)
        self.cs(0)
        self.bus.write(bytearray([color >> 8, color & 0xFF]))
        self.cs(1)

    def draw_square(self, x, y, s, color):
        self.set_windows(x, y, x + s, y + s)
        self.dc(1)
        self.cs(0)
        pixel = bytearray([color >> 8, color & 0xFF])
        for _ in range((s + 1) * (s + 1)):
            self.bus.write(pixel)
        self.cs(1)

    def lcd_fill(self, color):
        line = bytearray([color >> 8, color & 0xFF] * LCD_WIDTH)
        self.set_windows(0, 0, self.width - 1, self.height - 1)
        self.dc(1)
        self.cs(0)
        for _ in range(self.height):
            self.bus.write(line)
        self.cs(1)

    def fill_rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self.set_windows(x, y, x + w - 1, y + h - 1)
        line = bytearray([color >> 8, color & 0xFF] * w)
        self.dc(1)
        self.cs(0)
        for _ in range(h):
            self.bus.write(line)
        self.cs(1)

    def blit_rect(self, x, y, w, h, buf):
        """Push a pre-built RGB565 buffer (2*w*h bytes) at (x, y)."""
        if w <= 0 or h <= 0:
            return
        self.set_windows(x, y, x + w - 1, y + h - 1)
        self.dc(1)
        self.cs(0)
        self.bus.write(buf)
        self.cs(1)
