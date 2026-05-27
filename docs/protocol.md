# Protocol

The OTP gadget's cryptographic protocol: how two devices share a one-time pad, and how individual messages are encoded, transmitted, and authenticated. The crypto core is intentionally tiny — pure XOR encryption plus an HMAC-SHA256 tag per message — so the security argument is short and inspectable.

## OTP key exchange

Key exchange requires only one card to change hands. No cables, no wireless.

**Protocol:**

1. Before meeting, Person A opens `Contacts` → `+ Add contact`, enters a name for the new contact, and confirms `Prepare exchange`. The device generates fresh random bytes (`X_A`) and writes them to `/exchange/X_own.bin` on A's card.
2. Person A ejects their card and hands it to Person B.
3. Person B inserts A's card into the guest slot on B's device. B's device processes the exchange in a streaming loop (the RP2350 only has ~520 KB of SRAM — the full 10 MB never exists in RAM at once):
   - Reads `X_A` from A's card in small chunks (e.g. 4 KB).
   - For each chunk: generates a matching chunk of `X_B` from the TRNG, XORs them, appends the result to `OTP.bin` on A's card, and feeds both the chunk and the result into a rolling SHA-256.
   - Writes the final SHA-256 digest into `OTP.bin`'s header and deletes `X_own.bin`.
   - Re-reads `OTP.bin` in chunks to split it and write B's own `/secret/` files (encrypted with B's key) per the pad split convention below.
4. Person A takes their card back and inserts it into their own device.
5. A's device verifies the OTP checksum (also via streaming SHA-256), then reads `OTP.bin` in chunks to split it into A's own `/secret/` (encrypted with A's key) per the pad split convention below, then wipes `/exchange/`.

**Recovery after power-off:** If A's device powers off at any point during the exchange, the device recovers gracefully on next boot (after PIN entry):

- **Only `X_own.bin` present** (powered off before handing card to B): device goes straight to Home. The pending exchange is shown in Contacts with a `Waiting…` badge; A can discard it from there.
- **`OTP.bin` present** (powered off after taking the card back from B but before finalizing): device shows a recovery screen — "Incomplete exchange with [name]. Finalize or Discard?" — before Home. Finalize completes step 5; Discard wipes `/exchange/` and abandons the exchange.

**Pad split convention:** The party who prepared the exchange (wrote `X_own.bin` first — "A") owns the first half of the OTP:

- A: `pad_send.bin = OTP[0:5MB]`, `pad_receive.bin = OTP[5MB:10MB]`
- B: `pad_send.bin = OTP[5MB:10MB]`, `pad_receive.bin = OTP[0:5MB]`

The role (preparer vs other) is recorded in `OTP.bin`'s header (see below) so each device knows which half to assign where. The split mirrors: whenever one party consumes bytes from `pad_send`, the other party consumes the matching bytes from `pad_receive`. No byte is ever used in both directions.

**`OTP.bin` header format (64 bytes, at offset 0):**

| Bytes | Field | Value |
|---|---|---|
| 0–3 | magic | ASCII `OTPG` |
| 4 | version | `0x01` |
| 5 | role | `0x00` = preparer (A), `0x01` = other (B) |
| 6–31 | reserved | `0x00` padding |
| 32–63 | SHA-256 digest | 32 bytes, computed over the payload only |

The 10 MB payload starts at offset 64. The SHA-256 digest covers the payload bytes only — **not** the header itself. Write order: B's device first writes a 64-byte placeholder header, streams the 10 MB payload while accumulating SHA-256, then seeks back to offset 0 and overwrites the header with the final values (magic, version, role, digest). A's device, on finalize, reads the header, streams SHA-256 over the payload, and compares against the stored digest.

Only one card changes hands. B's device writes only to the plaintext `/exchange/` staging area on A's card — never to A's encrypted `/secret/`. A's private OTP data is never accessible to B's device.

The XOR step ensures the shared OTP is at least as random as the better of the two TRNGs — neither party needs to fully trust the other's hardware.

**Pad size per exchange:** 10 MB fixed (5 MB per direction). Each direction holds ~5 million characters of capacity — enough for months of typical messaging before another meetup is needed.

## Message encoding

Messages are encoded as raw bytes before XOR-ing with the OTP. The crypto core operates purely on `bytes` with no knowledge of the encoding:

```python
def encrypt(plaintext_bytes: bytes, pad_bytes: bytes) -> bytes:
    return bytes(p ^ k for p, k in zip(plaintext_bytes, pad_bytes))

# Same function for decrypt
decrypt = encrypt
```

The UI layer is responsible for the conversion:

- **Input → bytes**: `message.encode("ascii")` (or `"utf-8"` in the future)
- **bytes → display**: `decoded.decode("ascii")` (or `"utf-8"` in the future)

For v1, printable ASCII (32–126) plus newline (`\n`, 0x0A) is supported. Upgrading to UTF-8 later only requires changing those two `.encode()`/`.decode()` calls — the crypto core is unaffected.

### Wire format

What is displayed (as a QR code or as text for manual entry) and what is scanned/typed on the receiver side:

```
[offset (4 bytes)] [length (2 bytes)] [ciphertext (length bytes)] [tag (8 bytes)]
```

- `offset`: starting byte in the receiver's `pad_receive` file — big-endian uint32.
- `length`: ciphertext length in bytes — big-endian uint16.
- `ciphertext`: `plaintext XOR pad_receive[offset : offset+length]`.
- `tag`: 8-byte HMAC-SHA256 authentication tag — see [Message authentication](#message-authentication). On mismatch the UI shows "Message authentication failed" and does not decode.

The entire frame is encoded as **uppercase hex** for both display and transmission (each byte → 2 chars from `0-9A-F`). Hex is chosen over raw binary or base64 because it's the same encoding for both channels — the QR code and the manually-typed form carry the exact same characters — and its small alphabet minimizes typos during manual entry.

### Message authentication

Each message is protected by an 8-byte HMAC-SHA256 tag, computed using an additional 8 pad bytes consumed alongside the ciphertext.

**Sender:**
```
mac_key  = pad_send[offset+length : offset+length+8]
tag      = HMAC-SHA256(key=mac_key, msg=offset || length || ciphertext)[:8]
watermark advances by len(plaintext) + 8
```

**Receiver:**
```
mac_key  = pad_receive[offset+length : offset+length+8]
expected = HMAC-SHA256(key=mac_key, msg=offset || length || ciphertext)[:8]
if expected != tag: show "Message authentication failed", do not decode
used range: [offset, offset + length + 8]
if Burn after reading is on and this is not a replay: scrub that receive-pad range
```

The MAC key is 8 secret, never-reused pad bytes. Without them an attacker cannot forge a valid tag for any modified ciphertext — OTP XOR-malleability (flipping bits in ciphertext to predictably flip bits in plaintext) is defeated.
