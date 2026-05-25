import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, emit, join_room

socketio = SocketIO()
limiter = Limiter(key_func=get_remote_address)

def _assert_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Required env var {key} is not set")
    return val


INSTANCES_FILE = "sim_state/instances.json"
CARDS_FILE = "sim_state/cards.json"
CARDS_DIR = "sim_state/cards"

DEVICE_TYPES = [
    {"id": "3.5inch", "label": "3.5-inch gadget", "width": 320, "height": 480},
]


@dataclass
class InstanceState:
    hal: object | None = None
    thread: threading.Thread | None = None
    powered: bool = False


_instances: dict[str, InstanceState] = {}


# --- Site session layer -------------------------------------------------------

SITE_NS = "/site"
SITE_SESSIONS_DIR = "sim_state/sessions"
SITE_FIXTURES_DIR = "firmware/sim/fixtures"


@dataclass
class SiteInstanceState:
    hal: object | None = None
    thread: threading.Thread | None = None


@dataclass
class QueueEntry:
    session_id: str
    arrival_ts: float
    starter_mode: str


@dataclass
class SiteSession:
    session_id: str
    alice: SiteInstanceState
    bob: SiteInstanceState
    starter_mode: str
    created_at: float
    last_activity: float
    connected_count: int = 0
    idle_timer: threading.Timer | None = None
    lifetime_timer: threading.Timer | None = None
    grace_timer: threading.Timer | None = None


_site_sessions: dict[str, SiteSession] = {}
_site_queue: deque = deque()
_site_pair_count: int = 0
_site_lock = threading.Lock()

# Set by create_app() from env vars
_max_concurrent_pairs: int = 50
_session_idle_timeout: int = 300
_session_max_lifetime: int = 600


# --- File helpers -------------------------------------------------------------

def _load_instances_file() -> list[dict]:
    try:
        with open(INSTANCES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_instances_file(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(INSTANCES_FILE), exist_ok=True)
    tmp = INSTANCES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, INSTANCES_FILE)


def _load_cards_file() -> list[dict]:
    try:
        with open(CARDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_cards_file(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(CARDS_FILE), exist_ok=True)
    tmp = CARDS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, CARDS_FILE)


def _card_dir(card_id: str) -> str:
    return os.path.join(CARDS_DIR, card_id)


def _make_id(name: str, existing_ids: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))
    if not slug:
        slug = "instance"
    candidate = slug
    n = 2
    while candidate in existing_ids:
        candidate = f"{slug}_{n}"
        n += 1
    return candidate


# --- Card in-use helpers -------------------------------------------------------

def _card_in_use_by(card_id: str) -> tuple[str, str] | None:
    """Return (instance_id, slot) of the instance that currently has card_id inserted, or None."""
    for entry in _load_instances_file():
        for slot_name in ("own", "guest"):
            if entry.get("slots", {}).get(slot_name) == card_id:
                return (entry["id"], slot_name)
    return None


# --- Slot state helpers -------------------------------------------------------

def _get_slot_data(instance_id: str) -> dict:
    """Returns {own: {card_id, card_name}|None, guest: {card_id, card_name}|None}"""
    entries = _load_instances_file()
    entry = next((e for e in entries if e["id"] == instance_id), None)
    if not entry:
        return {"own": None, "guest": None}
    slots = entry.get("slots", {"own": None, "guest": None})
    cards_map = {c["id"]: c for c in _load_cards_file()}
    result = {}
    for slot_name in ("own", "guest"):
        card_id = slots.get(slot_name)
        if card_id and card_id in cards_map:
            result[slot_name] = {"card_id": card_id, "card_name": cards_map[card_id]["name"]}
        else:
            result[slot_name] = None
    return result


# --- Power helpers ------------------------------------------------------------

def _run_main_loop(hal) -> None:
    from firmware.hal.sim import _PoweredOff
    from firmware.core.boot import main_loop
    try:
        main_loop(hal)
    except _PoweredOff:
        pass


def _do_power_on(instance_id: str) -> None:
    from firmware.hal.sim import SimHAL
    entries = _load_instances_file()
    entry = next(e for e in entries if e["id"] == instance_id)
    state = _instances[instance_id]

    slots = dict(entry.get("slots", {"own": None, "guest": None}))
    resolved = {"own": None, "guest": None}
    needs_save = False
    cards_map = {c["id"]: c for c in _load_cards_file()}

    for slot_name in ("own", "guest"):
        card_id = slots.get(slot_name)
        if not card_id:
            continue
        if card_id not in cards_map:
            slots[slot_name] = None
            needs_save = True
        else:
            resolved[slot_name] = _card_dir(card_id)

    if needs_save:
        for e in entries:
            if e["id"] == instance_id:
                e["slots"] = slots
        _save_instances_file(entries)

    hal = SimHAL(
        state_dir=entry["state_dir"],
        device_id=instance_id,
        own_card_path=resolved["own"],
        guest_card_path=resolved["guest"],
    )
    t = threading.Thread(target=_run_main_loop, args=(hal,), daemon=True)
    t.start()
    state.hal = hal
    state.thread = t
    state.powered = True

    slot_data = {}
    for slot_name in ("own", "guest"):
        card_id = slots.get(slot_name)
        if card_id and card_id in cards_map:
            slot_data[slot_name] = {"card_id": card_id, "card_name": cards_map[card_id]["name"]}
        else:
            slot_data[slot_name] = None
    socketio.emit("hw_response", {"action": "powered_on", "slots": slot_data}, room=instance_id)


def _do_power_off(instance_id: str) -> None:
    state = _instances[instance_id]
    if state.hal is not None:
        state.hal.stop()
    state.hal = None
    state.thread = None
    state.powered = False


# --- Slot action handlers -----------------------------------------------------

def _handle_slot_choose(device_id: str, state: InstanceState, data: dict) -> None:
    slot = data.get("slot")
    card_id = data.get("card_id")
    if slot not in ("own", "guest") or not card_id:
        return

    cards = _load_cards_file()
    cards_map = {c["id"]: c for c in cards}
    if card_id not in cards_map:
        emit("hw_response", {"action": "slot_error", "slot": slot, "error": "Card not found"})
        return

    holder = _card_in_use_by(card_id)
    if holder is not None and holder != (device_id, slot):
        emit("hw_response", {"action": "slot_error", "slot": slot, "error": "Card is in use"})
        return

    entries = _load_instances_file()
    entry = next((e for e in entries if e["id"] == device_id), None)
    if not entry:
        return
    slots = dict(entry.get("slots", {"own": None, "guest": None}))

    other_slot = "guest" if slot == "own" else "own"
    if slots.get(other_slot) == card_id:
        emit("hw_response", {"action": "slot_error", "slot": slot, "error": "Card already in other slot"})
        return

    card_path = _card_dir(card_id)

    if state.hal is not None:
        state.hal._sim_set_slot(slot, card_path)

    slots[slot] = card_id
    for e in entries:
        if e["id"] == device_id:
            e["slots"] = slots
    _save_instances_file(entries)

    emit("hw_response", {
        "action": "slot_chosen",
        "slot": slot,
        "card_id": card_id,
        "card_name": cards_map[card_id]["name"],
    })


def _handle_slot_eject(device_id: str, state: InstanceState, data: dict) -> None:
    slot = data.get("slot")
    if slot not in ("own", "guest"):
        return

    entries = _load_instances_file()
    entry = next((e for e in entries if e["id"] == device_id), None)
    if not entry:
        return
    slots = dict(entry.get("slots", {"own": None, "guest": None}))

    if state.hal is not None:
        state.hal._sim_set_slot(slot, None)

    slots[slot] = None
    for e in entries:
        if e["id"] == device_id:
            e["slots"] = slots
    _save_instances_file(entries)

    emit("hw_response", {"action": "slot_ejected", "slot": slot})


# --- Site session helpers -----------------------------------------------------

def _site_session_dir(session_id: str) -> str:
    return os.path.join(SITE_SESSIONS_DIR, session_id)


def _site_card_dir(session_id: str, role: str) -> str:
    return os.path.join(SITE_SESSIONS_DIR, session_id, "cards", f"{role}_card")


def _create_site_session(session_id: str, starter_mode: str) -> "SiteSession":
    """Create dirs, state, and timers for a session.

    Caller must already have incremented _site_pair_count under _site_lock
    before calling this function.
    """
    session_dir = _site_session_dir(session_id)
    alice_state = os.path.join(session_dir, "alice")
    bob_state = os.path.join(session_dir, "bob")
    alice_card = _site_card_dir(session_id, "alice")
    bob_card = _site_card_dir(session_id, "bob")

    for d in (alice_state, bob_state, alice_card, bob_card):
        os.makedirs(d, exist_ok=True)

    if starter_mode == "quick_demo":
        for role in ("alice", "bob"):
            fixture = os.path.join(SITE_FIXTURES_DIR, "quick_demo", f"{role}_card")
            dest = _site_card_dir(session_id, role)
            if os.path.isdir(fixture):
                shutil.copytree(fixture, dest, dirs_exist_ok=True)

    now = time.time()
    session = SiteSession(
        session_id=session_id,
        alice=SiteInstanceState(),
        bob=SiteInstanceState(),
        starter_mode=starter_mode,
        created_at=now,
        last_activity=now,
    )
    _site_sessions[session_id] = session

    def _hard_cap():
        _destroy_site_session(session_id, "lifetime_cap")

    lt = threading.Timer(_session_max_lifetime, _hard_cap)
    lt.daemon = True
    lt.start()
    session.lifetime_timer = lt

    _reset_idle_timer(session)
    return session


def _reset_idle_timer(session: "SiteSession") -> None:
    if session.idle_timer:
        session.idle_timer.cancel()

    def _idle_check():
        with _site_lock:
            s = _site_sessions.get(session.session_id)
            if s is None:
                return
            idle_for = time.time() - s.last_activity
            if idle_for < _session_idle_timeout:
                remaining = _session_idle_timeout - idle_for
                t = threading.Timer(remaining, _idle_check)
                t.daemon = True
                t.start()
                s.idle_timer = t
                return
        _destroy_site_session(session.session_id, "idle_timeout")

    t = threading.Timer(_session_idle_timeout, _idle_check)
    t.daemon = True
    t.start()
    session.idle_timer = t


def _destroy_site_session(session_id: str, reason: str) -> None:
    global _site_pair_count

    with _site_lock:
        session = _site_sessions.pop(session_id, None)
        if session is None:
            return
        _site_pair_count -= 1
        for timer in (session.idle_timer, session.lifetime_timer, session.grace_timer):
            if timer:
                timer.cancel()

    for inst in (session.alice, session.bob):
        if inst.hal:
            inst.hal.stop()
    for inst in (session.alice, session.bob):
        if inst.thread and inst.thread.is_alive():
            inst.thread.join(timeout=5)

    shutil.rmtree(_site_session_dir(session_id), ignore_errors=True)
    socketio.emit("session_ended", {"reason": reason}, room=session_id, namespace=SITE_NS)
    _try_promote_queue()


def _try_promote_queue() -> None:
    global _site_pair_count
    with _site_lock:
        if not _site_queue or _site_pair_count >= _max_concurrent_pairs:
            return
        entry = _site_queue.popleft()
        _site_pair_count += 1  # atomic reservation before releasing the lock

    _create_site_session(entry.session_id, entry.starter_mode)
    socketio.emit("promoted", {}, room=entry.session_id, namespace=SITE_NS)


def _start_site_device(session_id: str, session: "SiteSession", role: str) -> None:
    from firmware.hal.sim import SimHAL

    # Guard: session may have been destroyed between lock release and this call
    with _site_lock:
        if _site_sessions.get(session_id) is None:
            return

    inst = session.alice if role == "alice" else session.bob
    if inst.hal is not None:
        return

    state_dir = os.path.join(_site_session_dir(session_id), role)
    own_card = _site_card_dir(session_id, role)
    device_id = f"{session_id}_{role}"

    hal = SimHAL(
        state_dir=state_dir,
        device_id=device_id,
        own_card_path=own_card,
        guest_card_path=None,
        preunlocked=True,
        namespace=SITE_NS,
    )
    t = threading.Thread(target=_run_main_loop, args=(hal,), daemon=True)
    t.start()
    inst.hal = hal
    inst.thread = t


# --- Quick-demo fixture generation -------------------------------------------

def _ensure_quick_demo_fixtures() -> None:
    """Generate pre-paired Alice+Bob fixture cards on first run (~10 MB).

    Creates firmware/sim/fixtures/quick_demo/{alice_card,bob_card}/ with
    plaintext /secret/ files (preunlocked format - no AES). Only runs once;
    subsequent calls return immediately if the sentinel file already exists.
    """
    alice_dir = os.path.join(SITE_FIXTURES_DIR, "quick_demo", "alice_card")
    bob_dir   = os.path.join(SITE_FIXTURES_DIR, "quick_demo", "bob_card")
    sentinel  = os.path.join(alice_dir, "secret", "contacts", "aabbccdd", "pad_send.bin")
    if os.path.exists(sentinel):
        return

    print("[fixtures] Generating quick_demo fixtures (~10 MB) ...", flush=True)

    from firmware.core.crypto.master_key import make_verify_token
    _DUMMY = b'\x00' * 32
    _PAD   = 5 * 1024 * 1024

    pad_1 = os.urandom(_PAD)   # Alice → Bob direction (Alice sends, Bob receives)
    pad_2 = os.urandom(_PAD)   # Bob → Alice direction (Bob sends, Alice receives)

    for card_dir, contact_id, contact_name, send_pad, recv_pad in [
        (alice_dir, "aabbccdd", "Bob",   pad_1, pad_2),
        (bob_dir,   "11223344", "Alice", pad_2, pad_1),
    ]:
        secret  = os.path.join(card_dir, "secret")
        cdir    = os.path.join(secret, "contacts", contact_id)
        os.makedirs(cdir, exist_ok=True)

        with open(os.path.join(secret, "verify.bin"), "wb") as f:
            f.write(make_verify_token(_DUMMY))

        with open(os.path.join(secret, "contacts.json"), "w") as f:
            json.dump({
                "version": 1,
                "in_flight": None,
                "contacts": [{"id": contact_id, "name": contact_name, "created_at": 1700000000}],
            }, f)

        with open(os.path.join(cdir, "pad_send.bin"), "wb") as f:
            f.write(send_pad)
        with open(os.path.join(cdir, "pad_receive.bin"), "wb") as f:
            f.write(recv_pad)
        with open(os.path.join(cdir, "pad_send_watermark.txt"), "w") as f:
            f.write("0")
        with open(os.path.join(cdir, "pad_receive_used_ranges.json"), "w") as f:
            f.write("[]")

    print("[fixtures] quick_demo fixtures ready.", flush=True)


# --- Flask app ----------------------------------------------------------------

def create_app(async_mode: str = "threading") -> Flask:
    """Build the Flask app.

    `async_mode` is passed explicitly (no env default, per project rule):
    dev/Werkzeug uses "threading"; the gunicorn production entrypoint
    (`wsgi.py`) passes "gevent" to match its gevent-websocket worker.
    """
    global _max_concurrent_pairs, _session_idle_timeout, _session_max_lifetime

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev"

    allowed_origins = _assert_env("ALLOWED_ORIGINS").split(",")
    _max_concurrent_pairs = int(_assert_env("MAX_CONCURRENT_PAIRS"))
    _session_idle_timeout = int(_assert_env("SESSION_IDLE_TIMEOUT_SECONDS"))
    _session_max_lifetime = int(_assert_env("SESSION_MAX_LIFETIME_SECONDS"))

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    CORS(app, resources={r"/api/*": {"origins": allowed_origins}})
    limiter.init_app(app)
    # Served at /sim/socket.io (not the library default /socket.io): no proxy
    # path rewrite exists, so this must match the frontend SIM_URL pathname
    # (.../sim) and the admin hub client below.
    socketio.init_app(app, cors_allowed_origins=allowed_origins,
                      async_mode=async_mode, path="sim/socket.io")

    @app.route("/healthz")
    def healthz():
        # Liveness probe for Coolify. No DB / session access on purpose.
        return jsonify({"status": "ok"})

    @app.route("/")
    def hub():
        entries = _load_instances_file()
        power_map = {iid: state.powered for iid, state in _instances.items()}
        return render_template("hub.html", device_types=DEVICE_TYPES, instances=entries, power_map=power_map)

    @app.route("/device/<device_id>")
    def device(device_id: str):
        if device_id not in _instances:
            return "Instance not found", 404
        entries = _load_instances_file()
        entry = next((e for e in entries if e["id"] == device_id), None)
        if entry is None:
            return "Instance not found", 404
        device_type = next((dt for dt in DEVICE_TYPES if dt["id"] == entry["device_type"]), DEVICE_TYPES[0])
        slot_data = _get_slot_data(device_id)
        return render_template(
            "index.html",
            device_id=device_id,
            instance_name=entry["name"],
            width=device_type["width"],
            height=device_type["height"],
            powered=_instances[device_id].powered,
            own_card=slot_data["own"],
            guest_card=slot_data["guest"],
        )

    @app.route("/api/instances", methods=["POST"])
    def create_instance():
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        device_type_id = data.get("device_type", "3.5inch")
        if not name:
            return jsonify({"error": "name is required"}), 400
        if not any(dt["id"] == device_type_id for dt in DEVICE_TYPES):
            return jsonify({"error": "unknown device type"}), 400
        entries = _load_instances_file()
        existing_ids = {e["id"] for e in entries}
        instance_id = _make_id(name, existing_ids)
        state_dir = f"sim_state/{instance_id}"
        entry = {
            "id": instance_id,
            "name": name,
            "device_type": device_type_id,
            "state_dir": state_dir,
            "slots": {"own": None, "guest": None},
        }
        entries.append(entry)
        _save_instances_file(entries)
        _instances[instance_id] = InstanceState()
        return jsonify(entry), 201

    @app.route("/api/instances/<instance_id>", methods=["DELETE"])
    def delete_instance(instance_id: str):
        state = _instances.pop(instance_id, None)
        if state:
            if state.hal is not None:
                state.hal.stop()
        entries = _load_instances_file()
        entries = [e for e in entries if e["id"] != instance_id]
        _save_instances_file(entries)
        return "", 204

    @app.route("/api/instances/<instance_id>", methods=["PATCH"])
    def rename_instance(instance_id: str):
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        entries = _load_instances_file()
        for e in entries:
            if e["id"] == instance_id:
                e["name"] = name
        _save_instances_file(entries)
        return jsonify({"id": instance_id, "name": name}), 200

    @app.route("/api/cards", methods=["GET"])
    def list_cards():
        cards = _load_cards_file()
        result = [{"id": c["id"], "name": c["name"], "locked": _card_in_use_by(c["id"]) is not None} for c in cards]
        return jsonify(result)

    @app.route("/api/cards", methods=["POST"])
    def create_card():
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        cards = _load_cards_file()
        if any(c["name"].lower() == name.lower() for c in cards):
            return jsonify({"error": "a card with that name already exists"}), 400
        existing_ids = {c["id"] for c in cards}
        card_id = _make_id(name, existing_ids)
        os.makedirs(_card_dir(card_id), exist_ok=True)
        cards.append({"id": card_id, "name": name})
        _save_cards_file(cards)
        return jsonify({"id": card_id, "name": name}), 201

    @app.route("/api/cards/<card_id>", methods=["DELETE"])
    def delete_card(card_id: str):
        if _card_in_use_by(card_id) is not None:
            return jsonify({"error": "Card is currently in use"}), 409
        cards = _load_cards_file()
        if not any(c["id"] == card_id for c in cards):
            return "", 404
        cards = [c for c in cards if c["id"] != card_id]
        _save_cards_file(cards)
        d = _card_dir(card_id)
        if os.path.isdir(d):
            shutil.rmtree(d)
        return "", 204

    @socketio.on("connect")
    def handle_connect():
        device_id = request.args.get("device_id", "")
        if device_id:
            join_room(device_id)

    @socketio.on("touch")
    def handle_touch(data):
        device_id = data.get("device_id", "")
        state = _instances.get(device_id)
        if state and state.hal is not None:
            state.hal._set_pending_touch((int(data["x"]), int(data["y"])))

    @socketio.on("qr_submit")
    def handle_qr(data):
        device_id = data.get("device_id", "")
        state = _instances.get(device_id)
        if state and state.hal is not None:
            state.hal._set_pending_qr(data.get("value", "").strip() or None)

    @socketio.on("hw_action")
    def handle_hw(data):
        device_id = data.get("device_id", "")
        state = _instances.get(device_id)
        if state is None:
            return
        action = data.get("action")
        hal = state.hal

        if action == "power_on":
            if not state.powered:
                _do_power_on(device_id)
            else:
                slot_data = _get_slot_data(device_id)
                socketio.emit("hw_response", {"action": "powered_on", "slots": slot_data}, room=device_id)
            return

        if action == "power_off":
            if state.powered:
                _do_power_off(device_id)
            socketio.emit("hw_response", {"action": "powered_off"}, room=device_id)
            return

        if action == "slot_choose":
            _handle_slot_choose(device_id, state, data)
            return

        if action == "slot_eject":
            _handle_slot_eject(device_id, state, data)
            return

        if hal is None:
            return

        if action == "battery":
            hal._sim_set_battery(int(data.get("value", 80)), hal._charging)
        elif action == "charger":
            hal._sim_set_battery(hal._battery_pct, bool(data.get("value", True)))
        elif action == "trigger_error":
            hal._sim_trigger_error()

    # --- Site session HTTP endpoints ------------------------------------------

    @app.route("/api/site/session", methods=["POST"])
    @limiter.limit("10 per hour")
    def create_site_session_route():
        global _site_pair_count
        data = request.get_json(force=True, silent=True) or {}
        starter_mode = data.get("starter_mode", "walkthrough")
        if starter_mode not in ("quick_demo", "walkthrough"):
            return jsonify({"error": "invalid starter_mode"}), 400

        session_id = str(uuid.uuid4())

        queued = False
        with _site_lock:
            if _site_pair_count >= _max_concurrent_pairs:
                _site_queue.append(QueueEntry(
                    session_id=session_id,
                    arrival_ts=time.time(),
                    starter_mode=starter_mode,
                ))
                queued = True
                queue_pos = len(_site_queue)
            else:
                _site_pair_count += 1  # atomic reservation

        if queued:
            return jsonify({
                "session_id": session_id,
                "status": "queued",
                "queue_position": queue_pos,
            }), 200

        _create_site_session(session_id, starter_mode)
        return jsonify({"session_id": session_id, "status": "started"}), 201

    @app.route("/api/site/session/<session_id>", methods=["GET"])
    def get_site_session_route(session_id: str):
        with _site_lock:
            session = _site_sessions.get(session_id)
            if session:
                return jsonify({
                    "session_id": session_id,
                    "status": "started",
                    "expires_at": session.created_at + _session_max_lifetime,
                }), 200
            # Check queue
            for i, entry in enumerate(_site_queue):
                if entry.session_id == session_id:
                    return jsonify({
                        "session_id": session_id,
                        "status": "queued",
                        "queue_position": i + 1,
                    }), 200
        return jsonify({"error": "not found"}), 404

    # --- Site session SocketIO namespace --------------------------------------

    @socketio.on("connect", namespace=SITE_NS)
    def site_handle_connect():
        session_id = request.args.get("session_id", "")
        role = request.args.get("role", "")  # "alice", "bob", or "" for queue watchers

        if not session_id:
            return False

        # All connections join the session room for session-level events
        join_room(session_id)

        with _site_lock:
            session = _site_sessions.get(session_id)
            # Allow queue watchers (no role) to join even if not yet started
            in_queue = any(e.session_id == session_id for e in _site_queue)
            if session is None and not in_queue:
                return False
            if session is None:
                # Queued - just watching; no device to start
                return

            if role not in ("alice", "bob"):
                # Queue watcher connected after session started - send ready
                expires_at = session.created_at + _session_max_lifetime
                emit("session_ready", {"expires_at": expires_at})
                return

            join_room(f"{session_id}_{role}")
            if session.grace_timer:
                session.grace_timer.cancel()
                session.grace_timer = None
            session.connected_count += 1
            session.last_activity = time.time()
            expires_at = session.created_at + _session_max_lifetime

        _start_site_device(session_id, session, role)
        emit("session_ready", {"expires_at": expires_at})

    @socketio.on("disconnect", namespace=SITE_NS)
    def site_handle_disconnect():
        session_id = request.args.get("session_id", "")
        role = request.args.get("role", "")
        if not session_id or role not in ("alice", "bob"):
            return

        with _site_lock:
            session = _site_sessions.get(session_id)
            if session is None:
                return
            session.connected_count = max(0, session.connected_count - 1)
            if session.connected_count > 0:
                return
            # Both disconnected - start grace timer
            if session.grace_timer:
                session.grace_timer.cancel()
            grace = threading.Timer(10, _destroy_site_session, args=(session_id, "all_disconnected"))
            grace.daemon = True
            grace.start()
            session.grace_timer = grace

    @socketio.on("touch", namespace=SITE_NS)
    def site_handle_touch(data):
        session_id = request.args.get("session_id", "")
        role = request.args.get("role", "")
        with _site_lock:
            session = _site_sessions.get(session_id)
            if session is None:
                return
            inst = session.alice if role == "alice" else session.bob
            hal = inst.hal
            session.last_activity = time.time()
        if hal:
            touch_type = data.get("type", "down")
            if touch_type in ("down", "move"):
                hal._set_pending_touch((int(data["x"]), int(data["y"])))
            elif touch_type == "up":
                hal._set_pending_touch(None)

    @socketio.on("qr_scanned", namespace=SITE_NS)
    def site_handle_qr_scanned(data):
        """Frontend emits this when the cursor reticle clicks over the other device's screen."""
        session_id = request.args.get("session_id", "")
        role = request.args.get("role", "")
        with _site_lock:
            session = _site_sessions.get(session_id)
            if session is None:
                return
            inst = session.alice if role == "alice" else session.bob
            hal = inst.hal
            session.last_activity = time.time()
        if hal:
            hal._set_pending_qr(data.get("payload", "").strip() or None)

    @socketio.on("card_action", namespace=SITE_NS)
    def site_handle_card_action(data):
        """Frontend emits when a card is dragged into/out of a slot.

        Fields:
          role   - which device's slot is changing ("alice" | "bob")
          action - "insert" | "eject"
          slot   - "own" | "guest"

        Card identity is implicit: alice's card lives in alice_card/, bob's in bob_card/.
        Inserting alice's guest slot = bob's card goes into alice's guest slot.
        """
        session_id = request.args.get("session_id", "")
        role = data.get("role", "")
        action = data.get("action", "")
        slot = data.get("slot", "")
        if role not in ("alice", "bob") or action not in ("insert", "eject") or slot not in ("own", "guest"):
            return

        with _site_lock:
            session = _site_sessions.get(session_id)
            if session is None:
                return
            inst = session.alice if role == "alice" else session.bob
            session.last_activity = time.time()

        if action == "eject":
            if inst.hal:
                inst.hal._sim_set_slot(slot, None)
        else:
            # insert: own slot gets that role's card; guest slot gets the other role's card
            if slot == "own":
                card_path = _site_card_dir(session_id, role)
            else:
                other = "bob" if role == "alice" else "alice"
                card_path = _site_card_dir(session_id, other)
            if inst.hal:
                inst.hal._sim_set_slot(slot, card_path)

    return app


def _cleanup_stale_locks() -> None:
    if not os.path.isdir(CARDS_DIR):
        return
    for card_id in os.listdir(CARDS_DIR):
        lock = os.path.join(CARDS_DIR, card_id, ".mounted.lock")
        try:
            os.remove(lock)
        except OSError:
            pass


def _cleanup_stale_sessions() -> None:
    """Wipe any session dirs left over from a previous crashed run."""
    if os.path.isdir(SITE_SESSIONS_DIR):
        shutil.rmtree(SITE_SESSIONS_DIR)


def _startup() -> None:
    """One-time process startup: runs before the first request in both dev
    and production."""
    _cleanup_stale_locks()
    _cleanup_stale_sessions()
    _ensure_quick_demo_fixtures()
    entries = _load_instances_file()
    for entry in entries:
        _instances[entry["id"]] = InstanceState()


def build_app(async_mode: str = "threading") -> Flask:
    """Production entrypoint helper (used by `wsgi.py`): run startup, then
    build the app. No dev server, no port binding, no interactive prompts."""
    _startup()
    return create_app(async_mode=async_mode)


def run(port: int = 8080) -> None:
    _startup()
    app = create_app()
    print(f"Hub running at http://127.0.0.1:{port}")
    socketio.run(app, host="127.0.0.1", port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
