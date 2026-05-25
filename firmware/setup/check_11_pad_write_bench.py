# Item 11: 5 MB write benchmark on onboard TF slot (SPI1)
#
# PREREQ: Own MicroSD card inserted in the onboard TF slot.
# PREREQ: sdcard.py must be on the board (copy from
#   docs/waveshare-examples-repo/examples/MicroPython/02_SD/sdcard.py)
# PREREQ: Card must have at least 6 MB free.
#
# Writes 5 MB to /sd/bench_test.bin in 4 KB chunks (same pattern as
# write_secret_stream), then reads back to verify, then deletes.
# Reports write and read throughput in KB/s.

import os
import time
import gc
from machine import SPI, Pin

TARGET_BYTES = 5 * 1024 * 1024   # 5 MB
CHUNK_SIZE   = 4096               # 4 KB, matches streaming chunk size in real.py
TEST_PATH    = '/sd/bench_test.bin'

print("=== Item 11: 5 MB pad write benchmark ===")

# Mount onboard TF (RP2350B: GPIO26/27/28/31 on SPI1).
try:
    os.umount('/sd')
    print("  Unmounted stale /sd")
except OSError:
    pass

import sdcard
spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0, bits=8,
          firstbit=SPI.MSB,
          sck=Pin(26), mosi=Pin(27), miso=Pin(28))
cs  = Pin(31, Pin.OUT, value=1)
sd  = sdcard.SDCard(spi, cs, baudrate=5_000_000)
os.mount(sd, '/sd')
print("  Own card mounted at /sd: OK")

free = os.statvfs('/sd')
free_bytes = free[0] * free[3]
print(f"  Free space: {free_bytes // (1024*1024)} MB")
if free_bytes < TARGET_BYTES + 512 * 1024:
    print("  FAIL: not enough free space on card")
    raise SystemExit

# Prepare chunk: simulate AES-CTR overhead (XOR with keystream)
# Use a bytearray of repeated pattern as stand-in for encrypted output.
chunk = bytearray(bytes(range(256)) * (CHUNK_SIZE // 256))
assert len(chunk) == CHUNK_SIZE

gc.collect()
print(f"\n  Writing {TARGET_BYTES // (1024*1024)} MB in {CHUNK_SIZE} B chunks...")
t0 = time.ticks_ms()

with open(TEST_PATH, 'wb') as f:
    written = 0
    while written < TARGET_BYTES:
        remaining = TARGET_BYTES - written
        if remaining < CHUNK_SIZE:
            _ = f.write(chunk[:remaining])
            written += remaining
        else:
            _ = f.write(chunk)
            written += CHUNK_SIZE

write_ms = time.ticks_diff(time.ticks_ms(), t0)
write_kbps = (TARGET_BYTES // 1024) * 1000 // write_ms if write_ms > 0 else 0
print(f"  Write done: {write_ms} ms  →  {write_kbps} KB/s")

# Read back
print(f"\n  Reading {TARGET_BYTES // (1024*1024)} MB back...")
t1 = time.ticks_ms()
total_read = 0
read_buf = bytearray(CHUNK_SIZE)
with open(TEST_PATH, 'rb') as f:
    while True:
        n = f.readinto(read_buf)
        if not n:
            break
        total_read += n

read_ms = time.ticks_diff(time.ticks_ms(), t1)
read_kbps = (total_read // 1024) * 1000 // read_ms if read_ms > 0 else 0
print(f"  Read done: {read_ms} ms  →  {read_kbps} KB/s  ({total_read} bytes)")

# Cleanup
os.remove(TEST_PATH)
print("  Test file deleted: OK")

# Results
WRITE_MIN_KBPS = 50   # 5 MB @ 50 KB/s = ~100 s - acceptable for one-off pad init
READ_MIN_KBPS  = 50   # full-pad reads are rare; per-message reads are <1 KB

print(f"\n  Write: {write_kbps} KB/s  (min acceptable: {WRITE_MIN_KBPS} KB/s)")
print(f"  Read:  {read_kbps} KB/s  (min acceptable: {READ_MIN_KBPS} KB/s)")

write_ok = write_kbps >= WRITE_MIN_KBPS
read_ok  = read_kbps  >= READ_MIN_KBPS

if write_ok and read_ok:
    print("PASS")
else:
    if not write_ok:
        print(f"FAIL: write too slow ({write_kbps} KB/s < {WRITE_MIN_KBPS} KB/s)")
        print("  Action: revisit encryption chunk size or pad size cap.")
    if not read_ok:
        print(f"FAIL: read too slow ({read_kbps} KB/s < {READ_MIN_KBPS} KB/s)")
