import json

from firmware.core.contacts_store import paths_for

PAD_SIZE = 5 * 1024 * 1024  # 5 MB per direction; single source of truth


def read_watermark(hal, contact_id: str) -> int:
    path = paths_for(contact_id)["pad_send_watermark"]
    try:
        return int(hal.read_secret(path).decode("ascii").strip())
    except OSError:
        return 0


def advance_watermark(hal, contact_id: str, by: int) -> int:
    new_val = read_watermark(hal, contact_id) + by
    hal.write_secret(paths_for(contact_id)["pad_send_watermark"], str(new_val).encode("ascii"))
    return new_val


def read_used_ranges(hal, contact_id: str) -> list:
    path = paths_for(contact_id)["pad_receive_used_ranges"]
    try:
        return json.loads(hal.read_secret(path).decode("ascii"))
    except OSError:
        return []


def is_replay(used_ranges: list, start: int, end: int) -> bool:
    for r_start, r_end in used_ranges:
        if start < r_end and end > r_start:
            return True
    return False


def append_used_range(hal, contact_id: str, start: int, end: int) -> list:
    merged = _merge(read_used_ranges(hal, contact_id) + [[start, end]])
    hal.write_secret(paths_for(contact_id)["pad_receive_used_ranges"], json.dumps(merged).encode("ascii"))
    return merged


def init_bookkeeping(hal, contact_id: str) -> None:
    """Write fresh watermark=0 and empty used_ranges. Called by FinalizeExchangeScreen."""
    p = paths_for(contact_id)
    hal.write_secret(p["pad_send_watermark"], b"0")
    hal.write_secret(p["pad_receive_used_ranges"], b"[]")


def _merge(ranges: list) -> list:
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda r: r[0])
    out = [ranges[0][:]]
    for start, end in ranges[1:]:
        if start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out
