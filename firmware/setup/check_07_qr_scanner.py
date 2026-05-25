# Item 7: QR scanner (GM861XS) heartbeat over UART1 (GPIO4 TX, GPIO5 RX)
#
# PREREQ: GM861XS wired as follows:
#   TX (scanner) → GPIO5 (board RX, header pin 16)
#   RX (scanner) → GPIO4 (board TX, header pin 14)
#   3.3V → pin 31 or 32
#   GND  → pin 29 or 30
#
# Heartbeat command: 7E 00 0A 01 00 00 00 30 1A
# Expected response: 03 00 00 01 00 33 31

import time
from machine import UART, Pin

UART_ID  = 1
TX_PIN   = 4
RX_PIN   = 5
BAUD     = 9600
TIMEOUT  = 2000  # ms

HEARTBEAT_CMD = bytes([0x7E, 0x00, 0x0A, 0x01, 0x00, 0x00, 0x00, 0x30, 0x1A])
HEARTBEAT_RSP = bytes([0x03, 0x00, 0x00, 0x01, 0x00, 0x33, 0x31])

print("=== Item 7: QR scanner heartbeat on UART1 (GPIO4/5) ===")

try:
    uart = UART(UART_ID, baudrate=BAUD, tx=Pin(TX_PIN), rx=Pin(RX_PIN),
                timeout=TIMEOUT)
    print("  UART1 init: OK")

    uart.write(HEARTBEAT_CMD)
    print("  Heartbeat sent:", HEARTBEAT_CMD.hex())

    deadline = time.ticks_add(time.ticks_ms(), TIMEOUT)
    buf = bytearray()
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        chunk = uart.read(32)
        if chunk:
            buf.extend(chunk)
            if len(buf) >= len(HEARTBEAT_RSP):
                break
        time.sleep_ms(20)

    print("  Response received:", buf.hex() if buf else "(none)")

    if buf[:len(HEARTBEAT_RSP)] == HEARTBEAT_RSP:
        print("PASS: scanner responded with expected heartbeat reply")
    elif buf:
        print("FAIL: unexpected response - check baud rate or scanner config")
    else:
        print("FAIL: no response within timeout")
        print("  Check TX/RX wiring and that scanner is powered.")

except Exception as e:
    print("FAIL:", type(e).__name__, e)
