from .text import draw_text

_BTN_BG   = 0x4208  # dark grey
_BTN_TEXT = 0xFFFF  # white


class Button:
    def __init__(self, x, y, w, h, label, font, color=_BTN_TEXT, bg=_BTN_BG):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.label = label
        self.font = font
        self.color = color
        self.bg = bg

    def draw(self, hal):
        hal.fill_rect(self.x, self.y, self.w, self.h, self.bg)
        glyph_w = self.font.max_width()
        glyph_h = self.font.height()
        text_w = len(self.label) * glyph_w
        tx = self.x + (self.w - text_w) // 2
        ty = self.y + (self.h - glyph_h) // 2
        draw_text(hal, tx, ty, self.label, self.font, self.color, self.bg)

    def hit_test(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h
