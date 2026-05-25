def text_width(text, font):
    return sum(font.get_ch(ch)[2] for ch in text)


def draw_text_centered(hal, y, text, font, color, bg=0x0000, screen_w=320):
    x = max(0, (screen_w - text_width(text, font)) // 2)
    draw_text(hal, x, y, text, font, color, bg)


def draw_text(hal, x, y, text, font, color, bg=0x0000):
    """Render text left-to-right at (x, y) using the given font module.

    color and bg are RGB565 integers. bg defaults to black.
    Newlines are not handled - caller must split lines and adjust y.
    """
    fg_hi = (color >> 8) & 0xFF
    fg_lo = color & 0xFF
    bg_hi = (bg >> 8) & 0xFF
    bg_lo = bg & 0xFF

    cx = x
    for ch in text:
        glyph, h, w = font.get_ch(ch)
        bytes_per_row = (w - 1) // 8 + 1
        buf = bytearray(w * h * 2)
        for row in range(h):
            row_off = row * bytes_per_row
            for col in range(w):
                bit = (glyph[row_off + col // 8] >> (7 - col % 8)) & 1
                px = (row * w + col) * 2
                if bit:
                    buf[px] = fg_hi
                    buf[px + 1] = fg_lo
                else:
                    buf[px] = bg_hi
                    buf[px + 1] = bg_lo
        hal.blit_rect(cx, y, w, h, bytes(buf))
        cx += w
