import sys
import types


class _SocketStub:
    def emit(self, *args, **kwargs):
        pass


_fake_app = types.ModuleType("firmware.sim.app")
_fake_app.socketio = _SocketStub()
sys.modules.setdefault("firmware.sim.app", _fake_app)

from firmware.hal.sim import SimHAL


def test_sim_overwrite_secret_slice_updates_decrypted_bytes(tmp_path):
    card = tmp_path / "card"
    (card / "secret").mkdir(parents=True)
    hal = SimHAL(state_dir=str(tmp_path / "state"), own_card_path=str(card))
    hal.unlock_secrets(b"\x11" * 32)

    path = "/secret/pad.bin"
    hal.write_secret(path, b"abcdefghijklmnopqrstuvwxyz")
    hal.overwrite_secret_slice(path, 5, b"-----")

    assert hal.read_secret(path) == b"abcde-----klmnopqrstuvwxyz"


def test_sim_overwrite_secret_slice_handles_unaligned_offsets(tmp_path):
    card = tmp_path / "card"
    (card / "secret").mkdir(parents=True)
    hal = SimHAL(state_dir=str(tmp_path / "state"), own_card_path=str(card))
    hal.unlock_secrets(b"\x22" * 32)

    path = "/secret/pad.bin"
    original = bytes(range(64))
    hal.write_secret(path, original)
    hal.overwrite_secret_slice(path, 17, b"\xFF\xFE\xFD")

    expected = original[:17] + b"\xFF\xFE\xFD" + original[20:]
    assert hal.read_secret(path) == expected
