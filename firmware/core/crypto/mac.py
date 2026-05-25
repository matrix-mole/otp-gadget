import struct

try:
    import hmac as _hmac
    import hashlib as _hashlib

    def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
        return _hmac.new(key, msg, _hashlib.sha256).digest()

except ImportError:
    import hashlib

    def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
        BLOCK = 64
        if len(key) > BLOCK:
            key = hashlib.sha256(key).digest()
        key = key + b"\x00" * (BLOCK - len(key))
        o_key = bytes(b ^ 0x5C for b in key)
        i_key = bytes(b ^ 0x36 for b in key)
        return hashlib.sha256(o_key + hashlib.sha256(i_key + msg).digest()).digest()


def compute_tag(mac_key: bytes, offset: int, length: int, ciphertext: bytes) -> bytes:
    msg = struct.pack(">IH", offset, length) + ciphertext
    return _hmac_sha256(mac_key, msg)[:8]


def verify_tag(mac_key: bytes, offset: int, length: int, ciphertext: bytes, tag: bytes) -> bool:
    expected = compute_tag(mac_key, offset, length, ciphertext)
    try:
        import hmac as _h
        return _h.compare_digest(expected, tag)
    except (ImportError, AttributeError):
        # Constant-time XOR comparison - no early exit
        if len(expected) != len(tag):
            return False
        diff = 0
        for a, b in zip(expected, tag):
            diff |= a ^ b
        return diff == 0
