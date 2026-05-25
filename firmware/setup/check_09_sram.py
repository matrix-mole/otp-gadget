# Item 9: Free SRAM after boot with representative allocations
#
# PREREQ: No external wiring or files needed.
#
# Simulates worst-case in-flight allocations:
#   - QR matrix for v21 (101×101 boolean list) - largest we ever render
#   - Keyboard state (row buffers for 4 rows × 10 keys)
#   - Streaming chunk buffer (4 KB)
#   - A 500-byte plaintext message in RAM
# Reports free SRAM before and after. Target: > 100 KB remaining.

import gc

gc.collect()
free_start = gc.mem_free()
print("=== Item 9: SRAM check ===")
print(f"  Free at start:         {free_start:,} bytes  ({free_start//1024} KB)")

# QR v21 matrix: 101 × 101 booleans.
# In MicroPython a list of 101 bools is ~5 bytes/bool on RP2350 (small int).
# Full matrix = 101*101 = 10201 cells ≈ 50 KB in the worst case.
qr_matrix = [[False] * 101 for _ in range(101)]
gc.collect()
after_qr = gc.mem_free()
print(f"  After QR matrix alloc: {after_qr:,} bytes  (used ~{(free_start - after_qr)//1024} KB)")

# Keyboard: 4 rows × 10 key label strings (~3 chars each) + hit-box tuples
keyboard_rows = [
    [("Q",10,0,40,56),("W",50,0,40,56),("E",90,0,40,56),("R",130,0,40,56),
     ("T",170,0,40,56),("Y",210,0,40,56),("U",250,0,40,56),("I",290,0,40,56),
     ("O",330,0,40,56),("P",370,0,40,56)],
    [("A",30,56,40,56),("S",70,56,40,56),("D",110,56,40,56),("F",150,56,40,56),
     ("G",190,56,40,56),("H",230,56,40,56),("J",270,56,40,56),("K",310,56,40,56),
     ("L",350,56,40,56),("⌫",390,56,50,56)],
    [("Z",10,112,40,56),("X",50,112,40,56),("C",90,112,40,56),("V",130,112,40,56),
     ("B",170,112,40,56),("N",210,112,40,56),("M",250,112,40,56)],
    [("123",10,168,60,56),(" ",80,168,180,56),("↵",270,168,60,56)],
]
gc.collect()
after_kb = gc.mem_free()
print(f"  After keyboard alloc:  {after_kb:,} bytes  (used ~{(after_qr - after_kb)//1024} KB)")

# 4 KB streaming chunk buffer
chunk = bytearray(4096)
gc.collect()
after_chunk = gc.mem_free()
print(f"  After 4 KB chunk buf:  {after_chunk:,} bytes  (used ~{(after_kb - after_chunk)//1024} KB)")

# 500-byte plaintext message
msg = bytearray(500)
gc.collect()
after_msg = gc.mem_free()
print(f"  After 500-byte msg:    {after_msg:,} bytes  (used ~{(after_chunk - after_msg)//1024} KB)")

# Cleanup and final
del qr_matrix, keyboard_rows, chunk, msg
gc.collect()
free_end = gc.mem_free()

print(f"\n  Peak usage (estimate): {(free_start - after_msg)//1024} KB")
print(f"  Minimum free observed: {after_msg:,} bytes  ({after_msg//1024} KB)")

THRESHOLD = 100 * 1024
if after_msg >= THRESHOLD:
    print(f"PASS: {after_msg//1024} KB free at peak load (threshold {THRESHOLD//1024} KB)")
else:
    print(f"FAIL: only {after_msg//1024} KB free - below {THRESHOLD//1024} KB threshold")
    print("  Action: shrink chunk sizes, avoid full keyboard row alloc, or use bytearray for QR.")
