from firmware.hal.real import RealHAL
from firmware.core.boot import main_loop


def _draw_error(hal, err):
    try:
        hal.fill_rect(0, 0, 320, 480, 0x0000)
        hal.fill_rect(0, 0, 320, 40, 0xF800)
        try:
            from firmware.core.widgets.text import draw_text
            from firmware.core.fonts import font_14
            y = 50
            for line in str(err).split("\n")[:20]:
                draw_text(hal, 4, y, line[:38], font_14, 0xFFFF)
                y += 18
        except Exception:
            pass
    except Exception:
        pass


hal = None
try:
    hal = RealHAL()
    # hal is module-level, so `import __main__; __main__.hal` reaches it from mpremote exec.
    main_loop(hal)
except Exception as e:
    if hal is not None:
        _draw_error(hal, e)
    raise
