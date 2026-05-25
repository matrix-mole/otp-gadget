"""
Pad-split convention (A/B symmetry).

From README:
  A (preparer): pad_send = OTP[0:5MB], pad_receive = OTP[5MB:10MB]
  B (other):    pad_send = OTP[5MB:10MB], pad_receive = OTP[0:5MB]

Key property: when A sends at offset k, A uses pad_send[k] = OTP[k].
              B receives at offset k, B uses pad_receive[k] = OTP[k].
              → same pad bytes → correct decryption.

              When B sends at offset k, B uses pad_send[k] = OTP[HALF+k].
              A receives at offset k, A uses pad_receive[k] = OTP[HALF+k].
              → same pad bytes → correct decryption.
"""
import os
from firmware.core.message import xor_pad


HALF = 5 * 1024 * 1024
OTP_SIZE = 2 * HALF


def _make_otp(size: int) -> bytes:
    return os.urandom(size)


def test_a_send_b_receive_symmetry():
    otp = _make_otp(OTP_SIZE)
    # Pads per the convention (slices, not copies - same reference bytes)
    a_pad_send    = otp[:HALF]
    b_pad_receive = otp[:HALF]

    offset = 42
    length = 10
    plaintext = b"helloworld"

    ciphertext = xor_pad(plaintext, a_pad_send[offset:offset + length])
    recovered  = xor_pad(ciphertext, b_pad_receive[offset:offset + length])
    assert recovered == plaintext


def test_b_send_a_receive_symmetry():
    otp = _make_otp(OTP_SIZE)
    b_pad_send    = otp[HALF:]
    a_pad_receive = otp[HALF:]

    offset = 0
    length = 15
    plaintext = b"secure message!"

    ciphertext = xor_pad(plaintext, b_pad_send[offset:offset + length])
    recovered  = xor_pad(ciphertext, a_pad_receive[offset:offset + length])
    assert recovered == plaintext


def test_no_pad_reuse_between_directions():
    """The two halves of the OTP are disjoint - send and receive use different bytes."""
    otp = _make_otp(OTP_SIZE)
    a_send    = otp[:HALF]
    a_receive = otp[HALF:]
    assert a_send != a_receive
    # A specific byte at offset k is different in each half (with overwhelming probability)
    assert a_send[0:32] != a_receive[0:32]


def test_otp_split_covers_full_payload():
    otp = _make_otp(OTP_SIZE)
    a_send    = otp[:HALF]
    a_receive = otp[HALF:]
    b_send    = otp[HALF:]
    b_receive = otp[:HALF]

    assert a_send == b_receive
    assert a_receive == b_send
    assert len(a_send) == HALF
    assert len(a_receive) == HALF


def test_xor_self_inverse():
    """Core OTP property: encrypt(decrypt(x)) == x."""
    pad = os.urandom(20)
    plaintext = b"test message text!!!"
    assert xor_pad(xor_pad(plaintext, pad), pad) == plaintext
