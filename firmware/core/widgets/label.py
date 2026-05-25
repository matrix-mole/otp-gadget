from .text import draw_text


class Label:
    def __init__(self, x, y, text, font, color, bg=0x0000):
        self.x = x
        self.y = y
        self.text = text
        self.font = font
        self.color = color
        self.bg = bg

    def draw(self, hal):
        draw_text(hal, self.x, self.y, self.text, self.font, self.color, self.bg)
