# Item 12: QR scanner scan-loop test (GM861XS) on UART1 (GPIO4 TX, GPIO5 RX)
#
# Triggers a scan, prints the decoded payload, repeats forever. Ctrl-C to stop.
# Run with:
#   mpremote connect <port> run firmware/setup/check_12_qr_scan_loop.py
#
# PREREQ: GM861XS wired as follows:
#   TX (scanner) -> GPIO5 (board RX, header pin 16)
#   RX (scanner) -> GPIO4 (board TX, header pin 14)
#   3.3V -> pin 31 or 32
#   GND  -> pin 29 or 30
#
# Configures the scanner once (manual trigger mode, CR/LF tail) using the same
# register writes as firmware/hal/real.py::_qr_ensure_config, then loops:
#   send trigger -> read until CR/LF -> print payload (text + hex)

import time
from machine import UART, Pin

UART_ID  = 1
TX_PIN   = 4
RX_PIN   = 5
BAUD     = 9600
TIMEOUT  = 500  # UART read timeout, ms

# Protocol frames (see GM861XS manual + hal/real.py)
TRIGGER       = bytes([0x7E, 0x00, 0x08, 0x01, 0x00, 0x02, 0x01, 0xAB, 0xCD])
TRIGGER_ACK   = bytes([0x02, 0x00, 0x00, 0x01, 0x00, 0x33, 0x31])
WRITE_ACK     = bytes([0x02, 0x00, 0x00, 0x01, 0x00, 0x33, 0x31])
SAVE_FLASH    = bytes([0x7E, 0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0xDE, 0xC8])

# CRC-CCITT (poly 0x1021, init 0, no reflect) - matches _qr_crc in real.py
def crc_ccitt(payload):
    crc = 0
    for b in payload:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return bytes([(crc >> 8) & 0xFF, crc & 0xFF])

def txn(uart, frame, expect_len, timeout_ms=500):
    uart.read()  # drain
    uart.write(frame)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    buf = b""
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        chunk = uart.read(expect_len - len(buf))
        if chunk:
            buf += chunk
            if len(buf) >= expect_len:
                return buf
        time.sleep_ms(10)
    raise OSError("short response: got {} bytes ({})".format(len(buf), buf.hex()))

def read_zone(uart, addr):
    body = bytes([0x07, 0x01, (addr >> 8) & 0xFF, addr & 0xFF, 0x01])
    frame = b"\x7E\x00" + body + crc_ccitt(body)
    rsp = txn(uart, frame, expect_len=7)
    if rsp[:4] != b"\x02\x00\x00\x01":
        raise OSError("read zone {:#06x}: bad header {}".format(addr, rsp.hex()))
    # Scanner CRCs response over bytes 1..5 (Lens+Address+Data), excluding the Type byte at index 0.
    if crc_ccitt(rsp[1:5]) != rsp[5:7]:
        raise OSError("read zone {:#06x}: bad CRC {}".format(addr, rsp.hex()))
    return rsp[4]

def write_zone(uart, addr, value):
    body = bytes([0x08, 0x01, (addr >> 8) & 0xFF, addr & 0xFF, value])
    frame = b"\x7E\x00" + body + crc_ccitt(body)
    rsp = txn(uart, frame, expect_len=7)
    if rsp != WRITE_ACK:
        raise OSError("write zone {:#06x}: bad ACK {}".format(addr, rsp.hex()))

def save_flash(uart):
    rsp = txn(uart, SAVE_FLASH, expect_len=7)
    if rsp != WRITE_ACK:
        raise OSError("save flash: bad ACK {}".format(rsp.hex()))

def ensure_config(uart):
    # Mirrors firmware/hal/real.py::_qr_ensure_config plus an output-routing fix.
    needs_save = False
    # Zone 0x0000 bits[1:0] = 01 -> Command Triggered. Preserve other bits.
    cur = read_zone(uart, 0x0000)
    target = (cur & 0xFC) | 0x01
    if cur != target:
        write_zone(uart, 0x0000, target)
        needs_save = True
    # Zone 0x000D bits[1:0] = 00 -> serial port output (not USB HID/virtual).
    # This is the crucial routing bit: if the scanner is shipped in HID-KBW
    # mode, decoded data goes to USB and never reaches the UART.
    cur = read_zone(uart, 0x000D)
    target = cur & 0xFC
    if cur != target:
        write_zone(uart, 0x000D, target)
        needs_save = True
    # Zone 0x0060 bits[6:5] = 01 (CR+LF tail), bit 0 = 1 (enable tail). Preserve rest.
    cur = read_zone(uart, 0x0060)
    target = (cur & 0x9E) | 0x21
    if cur != target:
        write_zone(uart, 0x0060, target)
        needs_save = True
    if needs_save:
        save_flash(uart)
        print("  scanner config updated and saved to flash")
    else:
        print("  scanner config already correct")

print("=== Item 12: QR scanner scan loop on UART1 (GPIO4/5) ===")
uart = UART(UART_ID, baudrate=BAUD, tx=Pin(TX_PIN), rx=Pin(RX_PIN), timeout=TIMEOUT)
print("  UART1 init: OK")

ensure_config(uart)

# Dump key registers so we can see exactly how the scanner is configured.
# 0x0000: read mode (bits 1:0), LED/lighting (bits 7-2)
# 0x0002: command trigger bit (bit 0) - should be 0 when idle
# 0x0006: single-read timeout (0x01..0xFF -> 0.1..25.5s, default 0x32 = 5s)
# 0x0013: same-barcode reading delay setting (bit 7)
# 0x0060: output protocol (bit 7), tail type (bits 6-5), allow-tail (bit 0)
for addr in (0x0000, 0x0002, 0x0006, 0x000D, 0x0013, 0x0060):
    val = read_zone(uart, addr)
    print("  zone {:#06x} = 0x{:02X} (0b{:08b})".format(addr, val, val))

print("\n  Ready. Point scanner at a QR code. Ctrl-C to stop.")
print("  Each scan attempt logs every byte received with a timestamp.\n")

SCAN_WINDOW_MS = 8_000  # listen for this long after each trigger, no early-exit

scan_count = 0
while True:
    uart.read()  # drain stale bytes
    t0 = time.ticks_ms()
    uart.write(TRIGGER)

    scan_count += 1
    print("--- scan #{} trigger sent ---".format(scan_count))
    deadline = time.ticks_add(t0, SCAN_WINDOW_MS)
    total = b""
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        chunk = uart.read(256)
        if chunk:
            elapsed = time.ticks_diff(time.ticks_ms(), t0)
            total += chunk
            print("  +{:>5}ms  recv {:>3}B: {}".format(elapsed, len(chunk), chunk.hex()))
        time.sleep_ms(20)

    if not total:
        print("  (no bytes at all)\n")
        continue

    payload = total
    if payload.startswith(TRIGGER_ACK):
        payload = payload[len(TRIGGER_ACK):]
    while payload and payload[-1:] in (b"\r", b"\n", b"\x00"):
        payload = payload[:-1]

    if payload:
        try:
            text = payload.decode("ascii")
            print("  >>> decoded: {!r}".format(text))
        except UnicodeError:
            print("  >>> non-ASCII payload, hex: {}".format(payload.hex()))
    else:
        print("  (only ACK/terminators in {}ms window)".format(SCAN_WINDOW_MS))
    print()
    time.sleep_ms(500)
