import struct as _struct

try:
    import cryptolib as _cl

    def _make_ecb_cipher(key: bytes):
        return _cl.aes(key, 1)

    def _ecb(key: bytes, block: bytes) -> bytes:
        return _cl.aes(key, 1).encrypt(block)

except ImportError:
    from Crypto.Cipher import AES as _AES

    def _make_ecb_cipher(key: bytes):
        return _AES.new(key, _AES.MODE_ECB)

    def _ecb(key: bytes, block: bytes) -> bytes:
        return _AES.new(key, _AES.MODE_ECB).encrypt(block)


_MOD = 1 << 128


def _counter_block(iv: bytes, index: int) -> bytes:
    n = (int.from_bytes(iv, "big") + index) % _MOD
    return n.to_bytes(16, "big")


def aes_ctr_xor(key: bytes, iv: bytes, data: bytes, block_index: int = 0) -> bytes:
    """Single-shot AES-CTR. Encrypts or decrypts `data` starting at `block_index`."""
    return CTRStream(key, iv, block_index).update(data)


class CTRStream:
    """Incremental AES-CTR encryptor/decryptor. Handles partial-block boundaries.

    Uses bulk ECB: all counter blocks for an incoming chunk are built into one
    bytearray and encrypted in a single cipher.encrypt() call.  MicroPython's
    cryptolib C source loops over all blocks internally (one Python→C transition
    per chunk instead of one per block), giving ~4.6× throughput vs per-block calls.
    The cipher object is cached on the instance for the stream's lifetime.
    """

    def __init__(self, key: bytes, iv: bytes, start_block_index: int = 0) -> None:
        self._key = key
        self._iv = iv
        self._block_index = start_block_index
        self._ks = b""  # leftover keystream bytes from the last partial block
        self._cipher = _make_ecb_cipher(key)

    def _keystream_bulk(self, n_blocks: int) -> bytes:
        """Return n_blocks of keystream via a single ECB call, advancing the counter."""
        buf = bytearray(n_blocks * 16)
        base = (int.from_bytes(self._iv, "big") + self._block_index) % _MOD
        for i in range(n_blocks):
            ctr = (base + i) % _MOD
            _struct.pack_into(">QQ", buf, i * 16, ctr >> 64, ctr & 0xFFFFFFFFFFFFFFFF)
        ks = self._cipher.encrypt(buf)
        self._block_index += n_blocks
        return ks

    def update(self, data: bytes) -> bytes:
        out = bytearray()
        pos = 0

        # Consume leftover keystream from the previous partial block.
        if self._ks:
            take = min(len(self._ks), len(data))
            for j in range(take):
                out.append(data[j] ^ self._ks[j])
            self._ks = self._ks[take:]
            pos = take

        remaining = len(data) - pos
        if remaining == 0:
            return bytes(out)

        # Bulk-encrypt all full blocks in one ECB call.
        n_full = remaining // 16
        if n_full > 0:
            n_bytes = n_full * 16
            ks_all = self._keystream_bulk(n_full)
            data_slice = data[pos : pos + n_bytes]
            xored = (
                int.from_bytes(data_slice, "big") ^ int.from_bytes(ks_all, "big")
            ).to_bytes(n_bytes, "big")
            out.extend(xored)
            pos += n_bytes

        # Handle partial tail (< 16 bytes); save unused keystream for next call.
        if pos < len(data):
            ks = self._cipher.encrypt(_counter_block(self._iv, self._block_index))
            self._block_index += 1
            tail = len(data) - pos
            for j in range(tail):
                out.append(data[pos + j] ^ ks[j])
            self._ks = ks[tail:]

        return bytes(out)
