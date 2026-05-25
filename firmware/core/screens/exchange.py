import time

from firmware.core import breadcrumb, bookkeeping
from firmware.core import contacts_store
from firmware.core.fonts import font_14, font_28
from firmware.core.widgets.button import Button
from firmware.core.widgets.text import draw_text, draw_text_centered

_BG   = 0x0000
_FG   = 0xFFFF
_TEAL = 0x0640
_GREY = 0x8C71
_DIM  = 0x2945

_EXCHANGE_SIZE  = 10 * 1024 * 1024  # 10 MB fixed per README
_HALF           = _EXCHANGE_SIZE // 2  # 5 MB per direction
_HEADER_SIZE    = 64
_CHUNK          = 4096
_PROGRESS_EVERY = 128 * _CHUNK      # redraw every 512 KB (~20 updates total)
_TOUCH_EVERY    = 8                 # poll touch every 8 chunks (~32 KB, ~100 ms on hw)


class PrepareExchangeScreen:

    def __init__(self, hal, session, contact_id: str):
        self._hal = hal
        self._session = session
        self._contact_id = contact_id

    def run(self) -> str:
        """Returns 'HOME' when done, 'LOCK' on auto-lock, 'CONTACTS' on cancel."""
        self._hal.notify_screen("PrepareExchange")
        breadcrumb.mark(self._hal, "PrepareExchange")
        hal = self._hal
        session = self._session

        _sim = getattr(hal, '_preunlocked', False)
        _n_checks = max(1, ((_EXCHANGE_SIZE + _CHUNK - 1) // _CHUNK + _TOUCH_EVERY - 1) // _TOUCH_EVERY)
        _sim_check_delay = (3.0 / _n_checks) if _sim else 0.0

        btn_cancel = Button(110, 420, 100, 40, "Cancel", font_14, _FG, _DIM)
        self._draw_progress(0, btn_cancel)

        written = 0
        next_draw = _PROGRESS_EVERY
        chunk_num = 0
        cancelled = False

        with hal.write_file_stream("own", "/exchange/X_own.bin") as wh:
            while written < _EXCHANGE_SIZE:
                n = min(_CHUNK, _EXCHANGE_SIZE - written)
                wh.write(hal.get_random_bytes(n))
                written += n
                chunk_num += 1

                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    hal.feed_watchdog()
                    if hal.power_button_pressed():
                        hal.power_off()
                    t = hal.get_touch()
                    if t is not None:
                        session.record_touch(hal)
                        if btn_cancel.hit_test(*t):
                            cancelled = True
                            break
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)

                if written >= next_draw or written == _EXCHANGE_SIZE:
                    breadcrumb.mark(hal, f"PrepareExchange.write_X_own@{written}/{_EXCHANGE_SIZE}")
                    self._draw_progress(written, btn_cancel)
                    next_draw = written + _PROGRESS_EVERY

        if cancelled:
            if hal.file_exists("own", "/exchange/X_own.bin"):
                hal.delete_file("own", "/exchange/X_own.bin")
            contacts_store.set_in_flight(hal, None)
            return "CONTACTS"

        self._draw_done()
        # Card is now ready to hand over. Distinct from the entry-time
        # PrepareExchange signal so the website walkthrough can wait for the
        # generation process to finish before asking the user to tap through.
        self._hal.notify_screen("ExchangePrepared")
        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                return "HOME"
            hal.feed_watchdog()
            time.sleep(0.05)

    def _draw_progress(self, written: int, btn_cancel) -> None:
        hal = self._hal
        mb_done  = written / (1024 * 1024)
        mb_total = _EXCHANGE_SIZE / (1024 * 1024)
        pct      = int(written * 100 / _EXCHANGE_SIZE)
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 200, "Generating random bytes...", font_14, _FG)
        draw_text_centered(hal, 228, f"{mb_done:.1f} MB / {mb_total:.0f} MB  ({pct}%)", font_14, _GREY)
        hal.fill_rect(20, 252, 280, 12, _DIM)
        filled = int(280 * pct / 100)
        if filled > 0:
            hal.fill_rect(20, 252, filled, 12, _TEAL)
        btn_cancel.draw(hal)

    def _draw_done(self) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 30, "Card is ready!", font_14, _FG)
        draw_text(hal, 4, 66, "What to do next:", font_14, _GREY)
        steps = [
            "1. Remove card from the left slot",
            "2. Hand it to your partner",
            "3. Partner inserts your card in the",
            "   front slot of their gadget",
            "4. Partner adds you as a new contact",
            "5. Partner removes and returns card",
            "6. Insert card back in left slot",
            "7. Enter PIN, then tap Complete setup",
        ]
        sy = 88
        for line in steps:
            draw_text(hal, 4, sy, line, font_14, _FG)
            sy += 18
        draw_text_centered(hal, 446, "Tap anywhere to go back", font_14, _GREY)


class FinalizeExchangeScreen:
    """Handles both sides of finalization: B (guest has X_own.bin) and A (own has OTP.bin)."""

    def __init__(self, hal, session, contact_id: str):
        self._hal = hal
        self._session = session
        self._contact_id = contact_id

    def run(self) -> str:
        """Returns 'HOME' or 'LOCK'."""
        self._hal.notify_screen("FinalizeExchange")
        breadcrumb.mark(self._hal, "FinalizeExchange")
        hal = self._hal
        if hal.mount_card("guest") == "MOUNTED":
            if hal.file_exists("guest", "/exchange/X_own.bin"):
                return self._run_b_side()
            return self._error("Your partner hasn't prepared\ntheir card yet.")
        if hal.file_exists("own", "/exchange/OTP.bin"):
            return self._run_a_side()
        return self._error("Nothing to complete here.")

    # ------------------------------------------------------------------ B side

    def _run_b_side(self) -> str:
        hal = self._hal
        if hal.free_space("guest") < _EXCHANGE_SIZE + _HEADER_SIZE:
            return self._error("Guest card has no space\nfor exchange")
        r = self._b_generate_otp()
        if r == "LOCK":
            return "LOCK"
        r = self._b_split_pads()
        if r == "LOCK":
            return "LOCK"
        self._commit_finalize()
        # B-side key exchange is fully done here. Distinct from the entry-time
        # "FinalizeExchange" nav so the demo site can detect *completion* (when
        # it is safe to return the card), not just the start.
        self._hal.notify_screen("ExchangeComplete")
        return self._done(
            "Exchange complete!",
            "Eject guest card and",
            "return it to your partner.",
        )

    def _b_generate_otp(self) -> str:
        import hashlib
        hal = self._hal
        session = self._session
        _sim = getattr(hal, '_preunlocked', False)
        _n_checks = max(1, ((_EXCHANGE_SIZE + _CHUNK - 1) // _CHUNK + _TOUCH_EVERY - 1) // _TOUCH_EVERY)
        _sim_check_delay = (5.5 / _n_checks) if _sim else 0.0
        sha = hashlib.sha256()
        written = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(1, "Generating keys...", 0, _EXCHANGE_SIZE)

        with hal.write_file_stream("guest", "/exchange/OTP.bin") as otp_wh:
            otp_wh.write(b'\x00' * _HEADER_SIZE)  # placeholder; overwritten below

            for xa_chunk in hal.read_file_stream("guest", "/exchange/X_own.bin", 0, _EXCHANGE_SIZE):
                xb = hal.get_random_bytes(len(xa_chunk))
                n = len(xa_chunk)
                otp = (int.from_bytes(xa_chunk, 'big') ^ int.from_bytes(xb, 'big')).to_bytes(n, 'big')
                otp_wh.write(otp)
                sha.update(otp)
                written += n
                chunk_num += 1
                hal.feed_watchdog()
                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    if hal.power_button_pressed():
                        hal.power_off()
                    session.record_touch(hal)
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)
                if written >= next_draw or written == _EXCHANGE_SIZE:
                    breadcrumb.mark(hal, f"FinalizeExchange.b_generate_otp@{written}/{_EXCHANGE_SIZE}")
                    self._draw_phase(1, "Generating keys...", written, _EXCHANGE_SIZE)
                    next_draw = written + _PROGRESS_EVERY

            digest = sha.digest()
            # role=0x01 means B ("other"); A is 0x00 ("preparer")
            header = b'OTPG' + bytes([0x01, 0x01]) + b'\x00' * 26 + digest
            otp_wh.seek(0)
            otp_wh.write(header)

        hal.delete_file("guest", "/exchange/X_own.bin")
        return "OK"

    def _b_split_pads(self) -> str:
        hal = self._hal
        session = self._session
        _sim = getattr(hal, '_preunlocked', False)
        _n_half_checks = max(1, ((_HALF + _CHUNK - 1) // _CHUNK + _TOUCH_EVERY - 1) // _TOUCH_EVERY)
        _sim_check_delay = (0.5 / _n_half_checks) if _sim else 0.0
        paths = contacts_store.paths_for(self._contact_id)

        # B is "other": pad_receive = OTP[0:5MB], pad_send = OTP[5MB:10MB]
        written = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(2, "Saving receive keys...", 0, _HALF)
        with hal.write_secret_stream(paths["pad_receive"]) as wh:
            for chunk in hal.read_file_stream("guest", "/exchange/OTP.bin", _HEADER_SIZE, _HALF):
                wh.write(chunk)
                written += len(chunk)
                chunk_num += 1
                hal.feed_watchdog()
                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    if hal.power_button_pressed():
                        hal.power_off()
                    session.record_touch(hal)
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)
                if written >= next_draw or written == _HALF:
                    breadcrumb.mark(hal, f"FinalizeExchange.b_split_pads_receive@{written}/{_HALF}")
                    self._draw_phase(2, "Saving receive keys...", written, _HALF)
                    next_draw = written + _PROGRESS_EVERY

        written = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(3, "Saving send keys...", 0, _HALF)
        with hal.write_secret_stream(paths["pad_send"]) as wh:
            for chunk in hal.read_file_stream("guest", "/exchange/OTP.bin", _HEADER_SIZE + _HALF, _HALF):
                wh.write(chunk)
                written += len(chunk)
                chunk_num += 1
                hal.feed_watchdog()
                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    if hal.power_button_pressed():
                        hal.power_off()
                    session.record_touch(hal)
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)
                if written >= next_draw or written == _HALF:
                    breadcrumb.mark(hal, f"FinalizeExchange.b_split_pads_send@{written}/{_HALF}")
                    self._draw_phase(3, "Saving send keys...", written, _HALF)
                    next_draw = written + _PROGRESS_EVERY

        bookkeeping.init_bookkeeping(hal, self._contact_id)
        return "OK"

    # ------------------------------------------------------------------ A side

    def _run_a_side(self) -> str:
        r = self._a_verify_checksum()
        if r == "LOCK":
            return "LOCK"
        if r == "ERROR":
            return "HOME"
        r = self._a_split_pads()
        if r == "LOCK":
            return "LOCK"
        self._commit_finalize()
        # Symmetric with the B-side signal: marks A-side finalize completion
        # (contact now Ready) for the demo site's onboarding checklist.
        self._hal.notify_screen("ExchangeComplete")
        return self._done(
            "Exchange complete!",
            "You can now send and",
            "receive messages.",
        )

    def _a_verify_checksum(self) -> str:
        import hashlib
        hal = self._hal
        session = self._session
        _sim = getattr(hal, '_preunlocked', False)
        _n_checks = max(1, ((_EXCHANGE_SIZE + _CHUNK - 1) // _CHUNK + _TOUCH_EVERY - 1) // _TOUCH_EVERY)
        _sim_check_delay = (2.0 / _n_checks) if _sim else 0.0

        # Read 64-byte header from own card's OTP.bin
        header_raw = b""
        for chunk in hal.read_file_stream("own", "/exchange/OTP.bin", 0, _HEADER_SIZE):
            header_raw += chunk
        if header_raw[:4] != b"OTPG":
            self._show_error("Key data is damaged.")
            return "ERROR"
        stored_digest = header_raw[32:64]

        # Stream SHA-256 over payload to verify
        sha = hashlib.sha256()
        verified = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(1, "Verifying SHA-256...", 0, _EXCHANGE_SIZE)

        for chunk in hal.read_file_stream("own", "/exchange/OTP.bin", _HEADER_SIZE, _EXCHANGE_SIZE):
            sha.update(chunk)
            verified += len(chunk)
            chunk_num += 1
            hal.feed_watchdog()
            if chunk_num % _TOUCH_EVERY == 0:
                if session.is_idle_expired(hal):
                    return "LOCK"
                if hal.power_button_pressed():
                    hal.power_off()
                session.record_touch(hal)
                if _sim_check_delay:
                    time.sleep(_sim_check_delay)
            if verified >= next_draw or verified == _EXCHANGE_SIZE:
                breadcrumb.mark(hal, f"FinalizeExchange.a_verify_checksum@{verified}/{_EXCHANGE_SIZE}")
                self._draw_phase(1, "Verifying SHA-256...", verified, _EXCHANGE_SIZE)
                next_draw = verified + _PROGRESS_EVERY

        if sha.digest() != stored_digest:
            self._show_error("Key data is damaged -\nrestart setup with your partner.")
            return "ERROR"
        return "OK"

    def _a_split_pads(self) -> str:
        hal = self._hal
        session = self._session
        _sim = getattr(hal, '_preunlocked', False)
        _n_half_checks = max(1, ((_HALF + _CHUNK - 1) // _CHUNK + _TOUCH_EVERY - 1) // _TOUCH_EVERY)
        _sim_check_delay = (1.0 / _n_half_checks) if _sim else 0.0
        paths = contacts_store.paths_for(self._contact_id)

        # A is "preparer": pad_send = OTP[0:5MB], pad_receive = OTP[5MB:10MB]
        written = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(2, "Saving send keys...", 0, _HALF)
        with hal.write_secret_stream(paths["pad_send"]) as wh:
            for chunk in hal.read_file_stream("own", "/exchange/OTP.bin", _HEADER_SIZE, _HALF):
                wh.write(chunk)
                written += len(chunk)
                chunk_num += 1
                hal.feed_watchdog()
                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    if hal.power_button_pressed():
                        hal.power_off()
                    session.record_touch(hal)
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)
                if written >= next_draw or written == _HALF:
                    breadcrumb.mark(hal, f"FinalizeExchange.a_split_pads_send@{written}/{_HALF}")
                    self._draw_phase(2, "Saving send keys...", written, _HALF)
                    next_draw = written + _PROGRESS_EVERY

        written = 0
        chunk_num = 0
        next_draw = _PROGRESS_EVERY
        self._draw_phase(3, "Saving receive keys...", 0, _HALF)
        with hal.write_secret_stream(paths["pad_receive"]) as wh:
            for chunk in hal.read_file_stream("own", "/exchange/OTP.bin", _HEADER_SIZE + _HALF, _HALF):
                wh.write(chunk)
                written += len(chunk)
                chunk_num += 1
                hal.feed_watchdog()
                if chunk_num % _TOUCH_EVERY == 0:
                    if session.is_idle_expired(hal):
                        return "LOCK"
                    if hal.power_button_pressed():
                        hal.power_off()
                    session.record_touch(hal)
                    if _sim_check_delay:
                        time.sleep(_sim_check_delay)
                if written >= next_draw or written == _HALF:
                    breadcrumb.mark(hal, f"FinalizeExchange.a_split_pads_receive@{written}/{_HALF}")
                    self._draw_phase(3, "Saving receive keys...", written, _HALF)
                    next_draw = written + _PROGRESS_EVERY

        bookkeeping.init_bookkeeping(hal, self._contact_id)
        hal.delete_file("own", "/exchange/OTP.bin")
        return "OK"

    # ------------------------------------------------------------------ shared

    def _commit_finalize(self) -> None:
        """Commit contact (kind=='add') or clear in_flight (kind=='reexchange') atomically."""
        hal = self._hal
        inf = contacts_store.get_in_flight(hal)
        if inf is None:
            return
        if inf["kind"] == "add":
            contacts_store.commit_contact(hal, self._contact_id, inf["name"], inf["started_at"])
        else:
            contacts_store.set_in_flight(hal, None)

    def _draw_phase(self, step: int, label: str, done: int, total: int) -> None:
        hal = self._hal
        mb_done  = done / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        pct = int(done * 100 / total) if total else 0
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text(hal, 4, 180, f"Step {step}/3", font_14, _GREY)
        draw_text(hal, 4, 204, label, font_14, _FG)
        draw_text(hal, 4, 228, f"{mb_done:.1f} MB / {mb_total:.0f} MB  ({pct}%)", font_14, _GREY)
        hal.fill_rect(20, 252, 280, 12, _DIM)
        filled = int(280 * pct / 100)
        if filled > 0:
            hal.fill_rect(20, 252, filled, 12, _TEAL)

    def _show_error(self, msg: str) -> None:
        """Draws an error screen; caller must wait for tap separately if needed."""
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        for i, line in enumerate(msg.split("\n")):
            draw_text(hal, 4, 196 + i * 20, line, font_14, _FG)
        draw_text(hal, 4, 260, "Tap anywhere to go back", font_14, _GREY)

    def _error(self, msg: str) -> str:
        """Shows error screen, waits for tap or auto-lock, returns 'HOME' or 'LOCK'."""
        self._show_error(msg)
        hal = self._hal
        session = self._session
        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                return "HOME"
            hal.feed_watchdog()
            time.sleep(0.05)

    def _done(self, line1: str, line2: str = "", line3: str = "") -> str:
        """Shows done screen, waits for tap or auto-lock, returns 'HOME' or 'LOCK'."""
        hal = self._hal
        session = self._session
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 180, line1, font_14, _FG)
        if line2:
            draw_text_centered(hal, 208, line2, font_14, _GREY)
        if line3:
            draw_text_centered(hal, 226, line3, font_14, _GREY)
        draw_text_centered(hal, 280, "Tap anywhere to go back", font_14, _GREY)
        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                return "HOME"
            hal.feed_watchdog()
            time.sleep(0.05)


class IncompleteExchangeScreen:

    def __init__(self, hal, session):
        self._hal = hal
        self._session = session

    def run(self) -> str:
        """Returns 'HOME' or 'LOCK'."""
        self._hal.notify_screen("IncompleteExchange")
        breadcrumb.mark(self._hal, "IncompleteExchange")
        hal = self._hal
        session = self._session

        inf = contacts_store.get_in_flight(hal)
        contact_name = inf["name"] if inf else "unknown contact"
        contact_id   = inf["id"]   if inf else None

        # Only offer Finalize when we know which contact this belongs to.
        btn_fin  = Button(40, 280, 240, 46, "Complete setup", font_14, _BG, _TEAL) if contact_id else None
        btn_disc = Button(40, 338, 240, 46, "Abandon",        font_14, _FG, _DIM)

        self._draw(contact_name, btn_fin, btn_disc)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is None:
                hal.feed_watchdog()
                time.sleep(0.05)
                continue
            session.record_touch(hal)
            x, y = t
            if btn_fin and btn_fin.hit_test(x, y):
                return FinalizeExchangeScreen(hal, session, contact_id).run()
            if btn_disc.hit_test(x, y):
                r = self._confirm_discard(contact_name)
                if r == "LOCK":
                    return "LOCK"
                if r == "YES":
                    contacts_store.set_in_flight(hal, None)
                    for f in ("/exchange/X_own.bin", "/exchange/OTP.bin"):
                        if hal.file_exists("own", f):
                            hal.delete_file("own", f)
                    if inf and inf.get("kind") == "add":
                        for path in contacts_store.paths_for(contact_id).values():
                            if hal.file_exists("own", path):
                                hal.delete_file("own", path)
                    return "HOME"
                self._draw(contact_name, btn_fin, btn_disc)

    def _draw(self, contact_name: str, btn_fin, btn_disc) -> None:
        hal = self._hal
        hal.fill_rect(0, 0, 320, 480, _BG)
        draw_text_centered(hal, 160, f"Key setup with {contact_name[:16]}", font_14, _FG)
        draw_text_centered(hal, 180, "is incomplete.", font_14, _FG)
        draw_text_centered(hal, 210, "Finish or discard?", font_14, _GREY)
        if btn_fin:
            btn_fin.draw(hal)
        btn_disc.draw(hal)

    def _confirm_discard(self, contact_name: str) -> str:
        """Returns 'YES', 'NO', or 'LOCK'."""
        hal     = self._hal
        session = self._session
        _MW, _MH = 304, 456
        _MX = (320 - _MW) // 2
        _MY = (480 - _MH) // 2
        _BD = 2

        hal.fill_rect(_MX, _MY, _MW, _MH, _BG)
        hal.fill_rect(_MX,             _MY,             _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY + _MH - _BD, _MW, _BD, _TEAL)
        hal.fill_rect(_MX,             _MY,             _BD, _MH, _TEAL)
        hal.fill_rect(_MX + _MW - _BD, _MY,             _BD, _MH, _TEAL)

        lines = [
            "Abandon setup?",
            "",
            f"The setup with {contact_name[:14]}",
            "will be deleted and the contact",
            "will not be added.",
        ]
        line_h = 18
        btn_h  = 46
        text_h = len(lines) * line_h
        text_y0 = _MY + (_MH - text_h - btn_h - 24) // 2
        for i, line in enumerate(lines):
            if line:
                draw_text_centered(hal, text_y0 + i * line_h, line, font_14, _FG)

        bw = (_MW - 48) // 2
        by = _MY + _MH - btn_h - 16
        btn_yes = Button(_MX + 16,          by, bw, btn_h, "Abandon",  font_14, _FG,  _DIM)
        btn_no  = Button(_MX + 16 + bw + 8, by, bw, btn_h, "Cancel",   font_14, _BG,  _TEAL)
        btn_yes.draw(hal)
        btn_no.draw(hal)

        while True:
            if session.is_idle_expired(hal):
                return "LOCK"
            hal.feed_watchdog()
            if hal.power_button_pressed():
                hal.power_off()
            t = hal.get_touch()
            if t is not None:
                session.record_touch(hal)
                x, y = t
                if btn_yes.hit_test(x, y):
                    return "YES"
                if btn_no.hit_test(x, y):
                    return "NO"
            hal.feed_watchdog()
            time.sleep(0.05)
