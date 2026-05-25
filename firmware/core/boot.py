import time

from firmware.core import breadcrumb


_SCREEN_W = 320
_SCREEN_H = 480
_SPLASH_BG = 0x4208  # dark gray (matches the driver's power-on fill)
_SPLASH_FG = 0x8410  # mid-gray - the "OTP" letters


def _letter(hal, ch, x, y, w, h, t, color):
    """Draw one block letter from rectangles. ch in 'OTP'."""
    if ch == "O":
        hal.fill_rect(x, y, w, t, color)              # top
        hal.fill_rect(x, y + h - t, w, t, color)      # bottom
        hal.fill_rect(x, y, t, h, color)              # left
        hal.fill_rect(x + w - t, y, t, h, color)      # right
    elif ch == "T":
        hal.fill_rect(x, y, w, t, color)              # top bar
        hal.fill_rect(x + (w - t) // 2, y, t, h, color)  # stem
    elif ch == "P":
        bowl = 2 * h // 3
        hal.fill_rect(x, y, t, h, color)              # left stem
        hal.fill_rect(x, y, w, t, color)              # top
        hal.fill_rect(x + w - t, y, t, bowl, color)   # bowl right
        hal.fill_rect(x, y + bowl - t, w, t, color)   # bowl bottom


def _splash(hal) -> None:
    """Gray screen + big gray 'OTP' stacked vertically, briefly, then boot."""
    hal.fill_rect(0, 0, _SCREEN_W, _SCREEN_H, _SPLASH_BG)
    lw, lh, t, gap = 150, 110, 24, 40
    total = 3 * lh + 2 * gap
    x = (_SCREEN_W - lw) // 2
    y0 = (_SCREEN_H - total) // 2
    for i, ch in enumerate("OTP"):
        _letter(hal, ch, x, y0 + i * (lh + gap), lw, lh, t, _SPLASH_FG)
    time.sleep(1.2)


def main_loop(hal) -> None:
    time.sleep(0.5)  # allow Flask + SocketIO to finish starting before first draw
    breadcrumb.boot_log(hal)
    _splash(hal)

    while True:
        session = None
        try:
            if not hal.flash_exists("device_secret.bin"):
                from firmware.core.screens.device_setup import DeviceSetupScreen
                DeviceSetupScreen(hal).run()
                continue

            status = hal.mount_card("own")
            if status != "MOUNTED":
                _draw_placeholder(hal, "No card - insert own SD card\ninto the slot on the right side")
                hal.feed_watchdog()
                if hal.power_button_pressed():
                    hal.power_off()
                time.sleep(1)
                continue

            if not hal.file_exists("own", "/secret/verify.bin"):
                from firmware.core.screens.card_init import CardInitScreen
                CardInitScreen(hal).run()
                hal.lock_secrets()
                continue

            # Authenticated session loop: PIN → Home → (auto-lock → PIN → Home …)
            from firmware.core.screens.home import HomeScreen
            from firmware.core.session import Session
            from firmware.core import contacts_store
            from firmware.core.screens.pin_recovery import FactoryResetDone

            _pin_entry(hal)
            breadcrumb.clear_trail()
            session = Session(hal)
            contacts_store.reconcile_in_flight(hal)

            if hal.file_exists("own", "/exchange/OTP.bin"):
                from firmware.core.screens.exchange import IncompleteExchangeScreen
                r = IncompleteExchangeScreen(hal, session).run()
                if r == "LOCK":
                    session.lock(hal)
                    breadcrumb.clear_trail()
                    session = Session(hal)
                    _pin_entry(hal)
                    breadcrumb.clear_trail()
                    contacts_store.reconcile_in_flight(hal)

            while True:
                result = HomeScreen(hal, session).run()
                session.lock(hal)
                breadcrumb.clear_trail()
                session = Session(hal)
                _pin_entry(hal)
                breadcrumb.clear_trail()
                contacts_store.reconcile_in_flight(hal)

        except FactoryResetDone:
            continue  # device_secret.bin gone → outer while True → DeviceSetup

        except Exception as e:
            trail = breadcrumb.recent_trail()
            breadcrumb.clear_trail()
            try:
                hal.lock_secrets()
            except Exception:
                pass
            if session is not None:
                try:
                    session.message_history.clear()
                except Exception:
                    pass
            eio = isinstance(e, OSError) and (
                getattr(e, "errno", None) == 5
                or (e.args and e.args[0] == 5)
            )
            if eio:
                error_msg = "Card removed or changed - reinsert and re-enter PIN"
            else:
                error_msg = type(e).__name__
            try:
                from firmware.core.screens.recovery import RecoveryScreen
                RecoveryScreen(hal, error_msg, trail).run()
            except Exception:
                pass  # WDT will reboot if display is also broken


def _pin_entry(hal) -> None:
    """Run PIN entry unless the HAL is in sim-only preunlocked mode."""
    if getattr(hal, '_preunlocked', False):
        return  # DEK already set; PIN screen is meaningless in this sim session
    from firmware.core.screens.pin_entry import PINEntryScreen
    PINEntryScreen(hal).run()


def _draw_placeholder(hal, msg: str) -> None:
    from firmware.core.fonts import font_14
    from firmware.core.widgets.text import draw_text_centered
    hal.fill_rect(0, 0, 320, 480, 0x0000)
    lines = msg.split("\n")
    y = 233 - (len(lines) - 1) * 9
    for line in lines:
        draw_text_centered(hal, y, line, font_14, 0x8C71)
        y += 18
