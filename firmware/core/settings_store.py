import json

_PATH = "/secret/settings.json"

DEFAULTS = {
    "burn_after_reading": False,
    "burn_after_reading_help_seen": False,
}


def read_settings(hal) -> dict:
    try:
        raw = json.loads(hal.read_secret(_PATH).decode("utf-8"))
    except OSError:
        raw = {}
    out = DEFAULTS.copy()
    for key, default in DEFAULTS.items():
        val = raw.get(key, default)
        out[key] = val if isinstance(val, bool) else default
    return out


def write_settings(hal, settings: dict) -> None:
    out = DEFAULTS.copy()
    for key, default in DEFAULTS.items():
        val = settings.get(key, default)
        out[key] = val if isinstance(val, bool) else default
    hal.write_secret(_PATH, json.dumps(out).encode("utf-8"))


def get_bool(hal, key: str) -> bool:
    return read_settings(hal)[key]


def set_bool(hal, key: str, value: bool) -> None:
    settings = read_settings(hal)
    settings[key] = bool(value)
    write_settings(hal, settings)
