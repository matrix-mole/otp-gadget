# Fonts

Pre-generated MicroPython font modules. Committed to the repo - do not regenerate unless the font or size changes.

## Index

- `font_14.py` - DejaVu Sans Mono, 14 px (messages, hex output, labels)
- `font_28.py` - DejaVu Sans Mono, 28 px (keyboard keys, headings)

## How to regenerate

Requires `freetype-py` (`pip3 install freetype-py`) and `font_to_py.py` from [peterhinch/micropython-font-to-py](https://github.com/peterhinch/micropython-font-to-py).

The output filename must start with a letter (tool validation), so run from this directory:

```sh
cd firmware/core/fonts

# Download font_to_py once
curl -sL https://raw.githubusercontent.com/peterhinch/micropython-font-to-py/master/font_to_py.py -o /tmp/font_to_py.py

# Copy font to a name without spaces (tool requirement)
cp "/path/to/DejaVuSansMono.ttf" /tmp/DejaVuSansMono.ttf

# Build charset: printable ASCII (32-126) + ↑↓ arrows used in ContactThread
python3 -c "print(''.join(chr(c) for c in range(32, 127)) + '↑↓', end='')" > /tmp/font_charset.txt

python3 /tmp/font_to_py.py -x -f /tmp/DejaVuSansMono.ttf 14 font_14.py -k /tmp/font_charset.txt
python3 /tmp/font_to_py.py -x -f /tmp/DejaVuSansMono.ttf 28 font_28.py -k /tmp/font_charset.txt
```

Flags: `-x` = horizontal bitmap mapping (row-by-row), `-f` = fixed-width pitch, `-k` = charset file.

The charset includes all printable ASCII plus `↑` and `↓` (used in the message thread prefix `S↑`/`R↓`). The generated files are sparse (only listed chars are stored), so adding these two glyphs adds negligible size.

Font source used: `DejaVu Sans Mono for Powerline.ttf` from `~/Library/Fonts/` (macOS). The standard `DejaVuSansMono.ttf` from the [DejaVu fonts project](https://dejavu-fonts.github.io/) produces identical glyphs for the ASCII range.

## Format

Each `.py` module exposes:

- `height()` - glyph height in pixels
- `max_width()` - glyph width in pixels (fixed, same for all chars)
- `hmap()` → `True` - rows are packed left-to-right, MSB first
- `reverse()` → `False` - 1 = lit pixel
- `get_ch(ch)` → `(bytearray, height, width)` - bitmap for one character
