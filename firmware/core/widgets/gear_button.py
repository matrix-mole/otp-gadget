# 20×20 gear icon, RGB565 big-endian. Background = _DIM (0x2945), icon = _FG (0xFFFF).
_W = 20
_H = 20
_DIM = 0x2945
_ICON = bytes.fromhex(
    "294529452945294529452945ffff29452945294529452945ffff2945294529452945294529452945"
    "2945294529452945ffffffffffff29452945294529452945ffffffffffff29452945294529452945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "2945294529452945ffffffffffffffffffffffffffffffffffffffffffff29452945294529452945"
    "2945294529452945ffffffffffffffffffffffffffffffffffffffffffff29452945294529452945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "ffffffffffffffffffffffffffffffff294529452945ffffffffffffffffffffffffffffffff2945"
    "ffffffffffffffffffffffffffff29452945294529452945ffffffffffffffffffffffffffff2945"
    "ffffffffffffffffffffffffffff29452945294529452945ffffffffffffffffffffffffffff2945"
    "ffffffffffffffffffffffffffff29452945294529452945ffffffffffffffffffffffffffff2945"
    "ffffffffffffffffffffffffffffffff294529452945ffffffffffffffffffffffffffffffff2945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "2945294529452945ffffffffffffffffffffffffffffffffffffffffffff29452945294529452945"
    "2945294529452945ffffffffffffffffffffffffffffffffffffffffffff29452945294529452945"
    "294529452945ffffffffffffffffffffffffffffffffffffffffffffffffffff2945294529452945"
    "2945294529452945ffffffffffff29452945294529452945ffffffffffff29452945294529452945"
    "294529452945294529452945ffff29452945294529452945ffff2945294529452945294529452945"
    "29452945294529452945294529452945294529452945294529452945294529452945294529452945"
)


class GearButton:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    def draw(self, hal):
        hal.fill_rect(self.x, self.y, self.w, self.h, _DIM)
        ix = self.x + (self.w - _W) // 2
        iy = self.y + (self.h - _H) // 2
        hal.blit_rect(ix, iy, _W, _H, _ICON)

    def hit_test(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h
