import json

_MANIFEST_PATH = "/secret/contacts.json"
_VERSION = 1


def _read(hal) -> dict:
    try:
        return json.loads(hal.read_secret(_MANIFEST_PATH).decode("utf-8"))
    except OSError:
        return {"version": _VERSION, "in_flight": None, "contacts": []}


def _write(hal, manifest: dict) -> None:
    hal.write_secret(_MANIFEST_PATH, json.dumps(manifest).encode("utf-8"))


def _sort_key(c: dict) -> tuple:
    return (c["created_at"], c["id"])


# ── Read-only queries ──────────────────────────────────────────────────────────

def list_contacts(hal) -> list:
    """Return all committed contacts sorted by created_at asc, id asc."""
    return sorted(_read(hal).get("contacts", []), key=_sort_key)


def get_in_flight(hal) -> dict | None:
    return _read(hal).get("in_flight")


def find_by_name_ci(hal, name: str) -> str | None:
    """Return id of any contact matching name (case-insensitive, trimmed), or None.
    Checks both committed contacts and in_flight.name."""
    needle = name.strip().lower()
    manifest = _read(hal)
    for c in manifest.get("contacts", []):
        if c["name"].strip().lower() == needle:
            return c["id"]
    inf = manifest.get("in_flight")
    if inf and inf["name"].strip().lower() == needle:
        return inf["id"]
    return None


def paths_for(contact_id: str) -> dict:
    base = f"/secret/contacts/{contact_id}"
    return {
        "pad_send":                f"{base}/pad_send.bin",
        "pad_receive":             f"{base}/pad_receive.bin",
        "pad_send_watermark":      f"{base}/pad_send_watermark.txt",
        "pad_receive_used_ranges": f"{base}/pad_receive_used_ranges.json",
    }


def pads_valid(hal, contact_id: str) -> bool:
    """True iff both pad files exist (atomic write guarantees no truncated state)."""
    p = paths_for(contact_id)
    return (
        hal.file_exists("own", p["pad_send"]) and
        hal.file_exists("own", p["pad_receive"])
    )


# ── Mutations ──────────────────────────────────────────────────────────────────

def init_manifest(hal) -> None:
    """Write a fresh empty contacts manifest. Called once at card init."""
    _write(hal, {"version": _VERSION, "in_flight": None, "contacts": []})


def commit_contact(hal, contact_id: str, name: str, created_at: int) -> None:
    """Append contact to manifest and clear in_flight atomically."""
    manifest = _read(hal)
    manifest["contacts"].append({"id": contact_id, "name": name, "created_at": created_at})
    manifest["in_flight"] = None
    _write(hal, manifest)


def delete_contact(hal, contact_id: str) -> None:
    """Remove contact from manifest. Caller must delete per-contact pad files separately."""
    manifest = _read(hal)
    manifest["contacts"] = [c for c in manifest["contacts"] if c["id"] != contact_id]
    _write(hal, manifest)


def set_in_flight(hal, struct) -> None:
    """Set or clear in_flight. struct is None or {id, name, started_at, kind}."""
    manifest = _read(hal)
    manifest["in_flight"] = struct
    _write(hal, manifest)


# ── Boot-time recovery ─────────────────────────────────────────────────────────

def reconcile_in_flight(hal) -> None:
    """Called once on boot after unlock. Silently cleans up stale in_flight markers
    from power-loss scenarios. Does nothing when staging files still exist (genuinely
    mid-flight exchange - IncompleteExchangeScreen handles that case)."""
    inf = get_in_flight(hal)
    if inf is None:
        return

    # Staging present → mid-flight, leave alone for IncompleteExchangeScreen
    if (
        hal.file_exists("own", "/exchange/X_own.bin") or
        hal.file_exists("own", "/exchange/OTP.bin")
    ):
        return

    contact_id = inf["id"]
    manifest = _read(hal)

    if inf["kind"] == "add" and pads_valid(hal, contact_id):
        # Power-loss after pad write but before manifest commit → commit now
        manifest["contacts"].append({
            "id": inf["id"],
            "name": inf["name"],
            "created_at": inf["started_at"],
        })

    manifest["in_flight"] = None
    _write(hal, manifest)
