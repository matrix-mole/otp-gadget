# 20×20 padlock icon. Background = _DIM (0x2945), icon = _FG (0xFFFF).
_W = 20
_H = 20
_DIM = 0x2945
_FG  = 0xFFFF

_LAYOUT = (
    "00000000000000000000",
    "00000011111111000000",  # shackle arch top
    "00000110000001100000",  # shackle sides (thick)
    "00000100000000100000",  # shackle sides (thin)
    "00000100000000100000",
    "00000100000000100000",
    "00000100000000100000",
    "00011111111111111000",  # body
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00011111111111111000",
    "00000000000000000000",
)

_buf = bytearray(_W * _H * 2)
_idx = 0
for _row in _LAYOUT:
    for _ch in _row:
        _c = _FG if _ch == "1" else _DIM
        _buf[_idx] = _c >> 8
        _buf[_idx + 1] = _c & 0xFF
        _idx += 2
_ICON = bytes(_buf)
del _buf, _idx, _row, _ch, _c


class LockButton:
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
