# Exchange speed benchmark
#
# Measures the individual components that determine key exchange performance.
# Run via:  ./scripts/bench.sh [--label <name>]
#
# PREREQ: Firmware deployed to the board (./scripts/flash.sh --label <name>).
# PREREQ: Own SD card inserted in the onboard TF slot (left slot in the case).
# PREREQ: Guest SD card is NOT required (only the own card is tested).
#
# The board's running firmware is interrupted; reset it afterwards with:
#   ./scripts/reset.sh --label <name>

import sys
import gc
import time
import os
import hashlib

# ── watchdog: keep alive if firmware had one running ─────────────────────────
# The firmware starts a hardware WDT (8 s) when /flash/watchdog.txt exists.
# mpremote interrupts the firmware but cannot stop a running WDT, so the
# board resets mid-benchmark if nobody feeds it.  Re-registering here with
# the same timeout takes ownership and lets us feed it inside every loop.
_wdt = None
try:
    os.stat('/flash/watchdog.txt')
    from machine import WDT as _WDT
    _wdt = _WDT(timeout=8000)
except Exception:
    pass

def _feed_wdt():
    if _wdt is not None:
        _wdt.feed()

# ── resolve sdcard driver from deployed firmware ──────────────────────────────

try:
    os.stat('/firmware/hal/drivers/sdcard.py')
except OSError:
    print("ERROR: /firmware/hal/drivers/sdcard.py not found.")
    print("Flash the firmware first:  ./scripts/flash.sh --label <name>")
    raise SystemExit(1)

sys.path.insert(0, '/firmware/hal/drivers')
import sdcard
from machine import SPI, SoftSPI, Pin

# ── constants ─────────────────────────────────────────────────────────────────

CHUNK     = 4096           # 4 KB - matches real.py streaming chunk size
MB        = 1024 * 1024

# ── helpers ───────────────────────────────────────────────────────────────────

def _kbps(n_bytes, ms):
    if ms <= 0:
        return 0
    return (n_bytes // 1024) * 1000 // ms

def _sep():
    print("=" * 62)

def _header(n, title):
    _sep()
    print(f"[{n}] {title}")
    _sep()

def _gc_collect():
    gc.collect()
    gc.collect()  # two passes to make the baseline cleaner

def _ticks():
    return time.ticks_ms()

def _elapsed(t0):
    return time.ticks_diff(time.ticks_ms(), t0)

# ── detect micropython native / viper support ─────────────────────────────────
# These are defined once here and reused in XOR and CTR sections.

_native_xor = None
_viper_xor  = None

try:
    import micropython as _mp

    @_mp.native
    def _do_native_xor(a, b):
        out = bytearray(len(a))
        for i in range(len(a)):
            out[i] = a[i] ^ b[i]
        return out

    _native_xor = _do_native_xor
except Exception:
    pass

try:
    import micropython as _mp2

    @_mp2.viper
    def _do_viper_xor(dst: ptr8, src: ptr8, n: int) -> None:  # noqa: F821
        for i in range(n):
            dst[i] ^= src[i]

    _viper_xor = _do_viper_xor
except Exception:
    pass

# ── AES ECB helper (same as ctr.py) ──────────────────────────────────────────

try:
    import cryptolib as _cl

    def _ecb_new(key, block):
        """New cipher object per call - current ctr.py style."""
        return _cl.aes(key, 1).encrypt(block)

    _cipher_cache = {}

    def _ecb_cached(key, block):
        """Reuse cipher object per key - potential optimisation."""
        k = id(key)
        if k not in _cipher_cache:
            _cipher_cache[k] = _cl.aes(key, 1)
        return _cipher_cache[k].encrypt(block)

    _ecb_available = True

except ImportError:
    # Sim / CPython fallback - not expected on hardware
    try:
        from Crypto.Cipher import AES as _AES

        def _ecb_new(key, block):
            return _AES.new(key, _AES.MODE_ECB).encrypt(block)

        _ecb_cached = _ecb_new
        _ecb_available = True
    except ImportError:
        _ecb_available = False

_MOD = 1 << 128

def _counter_block(iv, index):
    n = (int.from_bytes(iv, "big") + index) % _MOD
    return n.to_bytes(16, "big")

# ── SD card helpers ───────────────────────────────────────────────────────────

def _mount_own(baud_hz=5_000_000):
    try:
        os.umount('/sd')
    except OSError:
        pass
    spi = SPI(1, baudrate=baud_hz, polarity=0, phase=0,
              bits=8, firstbit=SPI.MSB,
              sck=Pin(26), mosi=Pin(27), miso=Pin(28))
    cs = Pin(31, Pin.OUT, value=1)
    sd = sdcard.SDCard(spi, cs, baudrate=baud_hz)
    os.mount(sd, '/sd')

def _unmount_own():
    try:
        os.umount('/sd')
    except OSError:
        pass

# ══════════════════════════════════════════════════════════════════════════════

print()
print("  OTP Gadget – Exchange Speed Benchmark")
print(f"  MicroPython {sys.version}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# [1]  TRNG throughput
# ─────────────────────────────────────────────────────────────────────────────

_header(1, "TRNG  –  os.urandom throughput")

try:
    os.urandom(1)
    _urandom = os.urandom
    print("  Source: os.urandom  (RP2350 hardware TRNG)")
except (AttributeError, OSError):
    import random as _rng

    def _urandom(n):
        buf = bytearray(n)
        i = 0
        while i + 4 <= n:
            v = _rng.getrandbits(32)
            buf[i] = v & 0xFF
            buf[i+1] = (v >> 8) & 0xFF
            buf[i+2] = (v >> 16) & 0xFF
            buf[i+3] = v >> 24
            i += 4
        while i < n:
            buf[i] = _rng.getrandbits(8)
            i += 1
        return bytes(buf)

    print("  Source: random.getrandbits  (TRNG fallback)")

TRNG_BYTES = 10 * MB
_gc_collect()
sys.stdout.write("  Measuring (. = 1 MB): ")
t0 = _ticks()
done = 0
while done < TRNG_BYTES:
    n = min(CHUNK, TRNG_BYTES - done)
    _ = _urandom(n)
    done += n
    _feed_wdt()
    if done % MB == 0:
        sys.stdout.write('.')
print()
ms = _elapsed(t0)
print(f"  10 MB in {ms} ms  →  {_kbps(TRNG_BYTES, ms)} KB/s")
del done, t0, ms

# ─────────────────────────────────────────────────────────────────────────────
# [2]  XOR variants
# ─────────────────────────────────────────────────────────────────────────────

_header(2, "XOR variants  –  4 KB chunks × 2560  (10 MB total)")
print("  (Fixes 4 + 5 in the plan target the worst-case variants)")
print()

XOR_CHUNKS = 2560
XOR_TOTAL  = XOR_CHUNKS * CHUNK
_a = bytes(range(256)) * (CHUNK // 256)
_b = bytes(reversed(range(256))) * (CHUNK // 256)

# 2a – bignum  (current code in exchange.py)
_gc_collect()
sys.stdout.write("  bignum (. = 1 MB):      ")
t0 = _ticks()
for _xi in range(XOR_CHUNKS):
    n = len(_a)
    _ = (int.from_bytes(_a, 'big') ^ int.from_bytes(_b, 'big')).to_bytes(n, 'big')
    _feed_wdt()
    if ((_xi + 1) * CHUNK) % MB == 0:
        sys.stdout.write('.')
print()
ms_bignum = _elapsed(t0)
print(f"  bignum  (current):      10 MB in {ms_bignum:7} ms  →  {_kbps(XOR_TOTAL, ms_bignum):5} KB/s")

# 2b – bytearray loop  (Fix 4)
_gc_collect()
sys.stdout.write("  bytearray (. = 1 MB):   ")
t0 = _ticks()
for _xi in range(XOR_CHUNKS):
    out = bytearray(_a)
    for i in range(len(out)):
        out[i] ^= _b[i]
    _feed_wdt()
    if ((_xi + 1) * CHUNK) % MB == 0:
        sys.stdout.write('.')
print()
ms_ba = _elapsed(t0)
speedup = ms_bignum / ms_ba if ms_ba > 0 else 0
print(f"  bytearray loop (Fix 4): 10 MB in {ms_ba:7} ms  →  {_kbps(XOR_TOTAL, ms_ba):5} KB/s  ({speedup:.1f}× vs bignum)")

# 2c – @micropython.native  (intermediate)
if _native_xor is not None:
    _gc_collect()
    sys.stdout.write("  @native (. = 1 MB):     ")
    t0 = _ticks()
    for _xi in range(XOR_CHUNKS):
        _ = _native_xor(_a, _b)
        _feed_wdt()
        if ((_xi + 1) * CHUNK) % MB == 0:
            sys.stdout.write('.')
    print()
    ms_nat = _elapsed(t0)
    speedup = ms_bignum / ms_nat if ms_nat > 0 else 0
    print(f"  @native                 10 MB in {ms_nat:7} ms  →  {_kbps(XOR_TOTAL, ms_nat):5} KB/s  ({speedup:.1f}× vs bignum)")
else:
    print("  @native:                not available on this build")

# 2d – @micropython.viper  (Fix 5)
if _viper_xor is not None:
    _gc_collect()
    sys.stdout.write("  @viper (. = 1 MB):      ")
    t0 = _ticks()
    for _xi in range(XOR_CHUNKS):
        out = bytearray(_a)
        _viper_xor(out, _b, len(out))
        _feed_wdt()
        if ((_xi + 1) * CHUNK) % MB == 0:
            sys.stdout.write('.')
    print()
    ms_viper = _elapsed(t0)
    speedup = ms_bignum / ms_viper if ms_viper > 0 else 0
    print(f"  @viper  (Fix 5):        10 MB in {ms_viper:7} ms  →  {_kbps(XOR_TOTAL, ms_viper):5} KB/s  ({speedup:.1f}× vs bignum)")
else:
    print("  @viper (Fix 5):         not available on this build")

del _a, _b, t0

# ─────────────────────────────────────────────────────────────────────────────
# [3]  AES-CTR throughput
# ─────────────────────────────────────────────────────────────────────────────

_header(3, "AES-CTR  –  manual ECB-based CTR, 1 MB in 4 KB chunks")
print("  (Relevant to the write side of all split phases)")
print("  (3c = new bulk-ECB variant not in the original benchmark)")
print()

CTR_BYTES = 1 * MB
N_CTR_CHUNKS = CTR_BYTES // CHUNK
_key = bytes(range(32))
_iv  = bytes(range(16))
_data = bytes(range(256)) * (CHUNK // 256)

if not _ecb_available:
    print("  SKIPPED: no AES implementation available")
else:
    # 3a – new cipher object per block  (current ctr.py style)
    _gc_collect()
    sys.stdout.write("  new cipher/block (. = 64 chunks): ")
    t0 = _ticks()
    blk = 0
    for _ci in range(N_CTR_CHUNKS):
        out = bytearray(CHUNK)
        pos = 0
        while pos < CHUNK:
            ks = _ecb_new(_key, _counter_block(_iv, blk))
            end = min(pos + 16, CHUNK)
            for j in range(end - pos):
                out[pos + j] = _data[pos + j] ^ ks[j]
            pos += 16
            blk += 1
        _feed_wdt()
        if (_ci + 1) % 64 == 0:
            sys.stdout.write('.')
    print()
    ms_ctr_new = _elapsed(t0)
    ecb_calls = N_CTR_CHUNKS * (CHUNK // 16)
    print(f"  new cipher/block (current): 1 MB in {ms_ctr_new} ms  →  {_kbps(CTR_BYTES, ms_ctr_new)} KB/s")
    print(f"  ({ecb_calls} ECB calls for 1 MB; each creates a new aes object)")

    # 3b – cached cipher object  (potential optimisation)
    _gc_collect()
    sys.stdout.write("  cached cipher obj (. = 64 chunks): ")
    t0 = _ticks()
    blk = 0
    for _ci in range(N_CTR_CHUNKS):
        out = bytearray(CHUNK)
        pos = 0
        while pos < CHUNK:
            ks = _ecb_cached(_key, _counter_block(_iv, blk))
            end = min(pos + 16, CHUNK)
            for j in range(end - pos):
                out[pos + j] = _data[pos + j] ^ ks[j]
            pos += 16
            blk += 1
        _feed_wdt()
        if (_ci + 1) % 64 == 0:
            sys.stdout.write('.')
    print()
    ms_ctr_cached = _elapsed(t0)
    speedup = ms_ctr_new / ms_ctr_cached if ms_ctr_cached > 0 else 0
    print(f"  cached cipher obj:          1 MB in {ms_ctr_cached} ms  →  {_kbps(CTR_BYTES, ms_ctr_cached)} KB/s  ({speedup:.1f}× vs new)")

    # 3c – bulk ECB: pass all 256 counter blocks for a 4 KB chunk as a single
    # encrypt() call.  MicroPython's cryptolib C source loops over multiple
    # blocks internally, so one Python→C call may process all 256 blocks
    # instead of making 256 separate round-trips.  If per-call overhead (not
    # raw AES throughput) dominates the 24 KB/s result, this could be a large
    # win.  Not tested in the original benchmark run.
    import struct as _struct
    try:
        # Probe: does encrypt() accept more than 16 bytes in ECB mode?
        _probe = _cl.aes(_key, 1).encrypt(bytes(32))  # 2 blocks
        if len(_probe) != 32:
            raise ValueError(f"unexpected output length {len(_probe)}")
        # Probe passed – run the timed test.
        _iv_int = int.from_bytes(_iv, 'big')
        _ctr_buf = bytearray(CHUNK)   # reusable counter-block buffer
        _bulk_cipher = _cl.aes(_key, 1)
        _gc_collect()
        sys.stdout.write("  bulk ECB (. = 64 chunks):  ")
        t0 = _ticks()
        _blk = 0
        for _ci in range(N_CTR_CHUNKS):
            # Fill _ctr_buf with 256 sequential counter blocks via struct
            # (avoids per-block bignum allocation of _counter_block()).
            for _bi in range(CHUNK // 16):
                _cv = (_iv_int + _blk + _bi) % _MOD
                _struct.pack_into(">QQ", _ctr_buf, _bi * 16,
                                  _cv >> 64, _cv & 0xFFFFFFFFFFFFFFFF)
            # Single ECB call → 4 KB of keystream (256 blocks processed in C).
            _ks = _bulk_cipher.encrypt(_ctr_buf)
            # XOR via bignum (proven fast at 776 KB/s).
            _ = (int.from_bytes(_data, 'big') ^ int.from_bytes(_ks, 'big')).to_bytes(CHUNK, 'big')
            _blk += CHUNK // 16
            _feed_wdt()
            if (_ci + 1) % 64 == 0:
                sys.stdout.write('.')
        print()
        ms_bulk = _elapsed(t0)
        speedup_bulk = ms_ctr_new / ms_bulk if ms_bulk > 0 else 0
        print(f"  bulk ECB (Fix 6):           1 MB in {ms_bulk} ms  →  {_kbps(CTR_BYTES, ms_bulk)} KB/s  ({speedup_bulk:.1f}× vs current)")
        del _ctr_buf, _bulk_cipher, _blk, _ks, _cv, _iv_int
    except NameError:
        print("  bulk ECB:               SKIPPED (cryptolib not available – sim/CPython)")
    except Exception as _be:
        print(f"  bulk ECB:               SKIPPED ({type(_be).__name__}: {_be})")

    _cipher_cache.clear()
    del _data, t0, blk, out

# ─────────────────────────────────────────────────────────────────────────────
# [4]  SHA-256 throughput
# ─────────────────────────────────────────────────────────────────────────────

_header(4, "SHA-256  –  streaming over 10 MB")
print("  (Used during key generation and A-side verification)")
print()

SHA_BYTES = 10 * MB
_sha_chunk = bytes(range(256)) * (CHUNK // 256)
_gc_collect()
sys.stdout.write("  Measuring (. = 1 MB): ")
t0 = _ticks()
sha = hashlib.sha256()
done = 0
while done < SHA_BYTES:
    sha.update(_sha_chunk)
    done += CHUNK
    _feed_wdt()
    if done % MB == 0:
        sys.stdout.write('.')
_ = sha.digest()
print()
ms_sha = _elapsed(t0)
print(f"  10 MB in {ms_sha} ms  →  {_kbps(SHA_BYTES, ms_sha)} KB/s")
del sha, done, t0, _sha_chunk

# ─────────────────────────────────────────────────────────────────────────────
# [5]  SD clock speed matrix  (Fix 3 in the plan)
# ─────────────────────────────────────────────────────────────────────────────

_header(5, "SD clock speed matrix  (own card, onboard TF on SPI1)")
print("  (Fix 3: raising the SDCard baudrate may give 2–5× improvement)")
print("  Testing 1 MB write + read at each speed. Stops at first failure.")
print()

SD_BENCH_BYTES = 1 * MB
_bench_chunk  = bytes(range(256)) * (CHUNK // 256)
_bench_path   = '/sd/_bench.bin'
_best_baud_hz = 5_000_000
_results      = []

BAUDS_HZ = [5_000_000, 10_000_000, 15_000_000, 20_000_000, 25_000_000]

for baud_hz in BAUDS_HZ:
    baud_mhz = baud_hz // 1_000_000
    try:
        _mount_own(baud_hz)
    except Exception as e:
        print(f"  {baud_mhz:2} MHz:  mount FAILED ({type(e).__name__}: {e})  ← stopping")
        break

    # write
    _gc_collect()
    t0 = _ticks()
    try:
        with open(_bench_path, 'wb') as f:
            written = 0
            while written < SD_BENCH_BYTES:
                f.write(_bench_chunk)
                written += CHUNK
                _feed_wdt()
    except Exception as e:
        _unmount_own()
        print(f"  {baud_mhz:2} MHz:  write FAILED ({type(e).__name__}: {e})  ← stopping")
        break
    ms_w = _elapsed(t0)

    # read
    _gc_collect()
    _rbuf = bytearray(CHUNK)
    t1 = _ticks()
    try:
        with open(_bench_path, 'rb') as f:
            while True:
                n = f.readinto(_rbuf)
                if not n:
                    break
                _feed_wdt()
    except Exception as e:
        _unmount_own()
        print(f"  {baud_mhz:2} MHz:  read FAILED ({type(e).__name__}: {e})  ← stopping")
        break
    ms_r = _elapsed(t1)

    try:
        os.remove(_bench_path)
    except OSError:
        pass
    _unmount_own()

    w_kbps = _kbps(SD_BENCH_BYTES, ms_w)
    r_kbps = _kbps(SD_BENCH_BYTES, ms_r)
    _results.append((baud_mhz, w_kbps, r_kbps))
    _best_baud_hz = baud_hz
    print(f"  {baud_mhz:2} MHz:  write {w_kbps:4} KB/s   read {r_kbps:4} KB/s   PASS")

if _results:
    _best = _results[-1]
    _base = _results[0]
    print()
    print(f"  → Best passing speed: {_best[0]} MHz")
    if len(_results) > 1:
        w_gain = _best[1] / _base[1] if _base[1] > 0 else 1.0
        r_gain = _best[2] / _base[2] if _base[2] > 0 else 1.0
        print(f"  → Gain vs 5 MHz:  write {w_gain:.1f}×  read {r_gain:.1f}×")

del _bench_chunk, _rbuf

# ─────────────────────────────────────────────────────────────────────────────
# [6]  Composite phase  –  read plaintext + AES-CTR encrypt + write
# ─────────────────────────────────────────────────────────────────────────────

_header(6, "Composite phase  –  read 2 MB → AES-CTR encrypt → write 2 MB")
print(f"  (Simulates one split-pad pass; using best SD speed from [5]: {_best_baud_hz // 1_000_000} MHz)")
print()

COMP_BYTES = 2 * MB
_src = '/sd/_bench_src.bin'
_dst = '/sd/_bench_dst.bin'

if not _ecb_available:
    print("  SKIPPED: no AES implementation available")
else:
    try:
        _mount_own(_best_baud_hz)
    except Exception as e:
        print(f"  SKIPPED: could not mount SD: {e}")
    else:
        # write source file
        _cdata = bytes(range(256)) * (CHUNK // 256)
        with open(_src, 'wb') as f:
            written = 0
            while written < COMP_BYTES:
                f.write(_cdata)
                written += CHUNK
                _feed_wdt()

        _ckey = bytes(range(32))
        _civ  = bytes(range(16))

        _gc_collect()
        t0 = _ticks()
        cblk = 0
        with open(_src, 'rb') as rf, open(_dst, 'wb') as wf:
            while True:
                chunk = rf.read(CHUNK)
                if not chunk:
                    break
                out = bytearray(len(chunk))
                pos = 0
                while pos < len(chunk):
                    ks = _ecb_new(_ckey, _counter_block(_civ, cblk))
                    end = min(pos + 16, len(chunk))
                    for j in range(end - pos):
                        out[pos + j] = chunk[pos + j] ^ ks[j]
                    pos += 16
                    cblk += 1
                wf.write(bytes(out))
                _feed_wdt()
        ms_comp = _elapsed(t0)

        for p in (_src, _dst):
            try:
                os.remove(p)
            except OSError:
                pass
        _unmount_own()

        eff = _kbps(COMP_BYTES, ms_comp)
        print(f"  2 MB (read + encrypt + write) in {ms_comp} ms  →  {eff} KB/s effective")
        print()

        # Extrapolate to full exchange phases using the measured composite rate
        # and the SD read rate from [5] (which bounds the single-pass gain).
        if eff > 0 and _results:
            r_kbps = _results[-1][2]
            print("  ── Extrapolated total exchange time ──────────────────────")
            # Current A-side: 20 MB of SD reads (3 passes over OTP.bin) + 10 MB AES writes
            current_read_s  = (20 * MB) / (r_kbps * 1024)
            current_write_s = (10 * MB) / (eff * 1024)
            current_s       = current_read_s + current_write_s
            print(f"  Current A-side (3 read passes × OTP.bin):")
            print(f"    reads  20 MB at {r_kbps} KB/s  →  {current_read_s:.0f} s")
            print(f"    writes 10 MB composite     →  {current_write_s:.0f} s")
            print(f"    total  ≈ {current_s:.0f} s  ({current_s/60:.1f} min)")
            print()
            # Fix 1+2 A-side: 10 MB reads (1 combined pass) + 10 MB AES writes
            fixed_read_s  = (10 * MB) / (r_kbps * 1024)
            fixed_write_s = current_write_s
            fixed_s       = fixed_read_s + fixed_write_s
            print(f"  Fixed A-side (Fix 1+2: 1 combined read pass):")
            print(f"    reads  10 MB at {r_kbps} KB/s  →  {fixed_read_s:.0f} s")
            print(f"    writes 10 MB composite     →  {fixed_write_s:.0f} s")
            print(f"    total  ≈ {fixed_s:.0f} s  ({fixed_s/60:.1f} min)")
            print()
            speedup = current_s / fixed_s if fixed_s > 0 else 0
            print(f"  A-side speedup from Fix 1+2 alone: {speedup:.1f}×")

        del _cdata, chunk, out, t0

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────

_sep()
print()
print("  Benchmark complete.")
print()
print("  Restart firmware:   ./scripts/reset.sh --label <name>")
print()
