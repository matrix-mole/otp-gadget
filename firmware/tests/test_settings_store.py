from firmware.core import settings_store


class _FakeHal:
    def __init__(self):
        self._secrets = {}

    def read_secret(self, path):
        if path not in self._secrets:
            raise OSError(path)
        return self._secrets[path]

    def write_secret(self, path, data):
        self._secrets[path] = data


def test_missing_settings_returns_defaults():
    hal = _FakeHal()
    settings = settings_store.read_settings(hal)
    assert settings["burn_after_reading"] is False
    assert settings["burn_after_reading_help_seen"] is False


def test_set_bool_preserves_other_settings():
    hal = _FakeHal()
    settings_store.set_bool(hal, "burn_after_reading_help_seen", True)
    settings_store.set_bool(hal, "burn_after_reading", True)
    settings = settings_store.read_settings(hal)
    assert settings["burn_after_reading"] is True
    assert settings["burn_after_reading_help_seen"] is True


def test_invalid_setting_values_fall_back_to_defaults():
    hal = _FakeHal()
    hal.write_secret(
        "/secret/settings.json",
        b'{"burn_after_reading": "yes", "burn_after_reading_help_seen": 1}',
    )
    settings = settings_store.read_settings(hal)
    assert settings["burn_after_reading"] is False
    assert settings["burn_after_reading_help_seen"] is False
