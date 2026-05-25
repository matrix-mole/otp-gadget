#!/usr/bin/env python3
"""End-to-end smoke test for the OTP sim backend live demo path.

Proves the parts a working public site depends on, in one run:

  1. GET  /healthz                 - server is up (liveness probe)
  2. POST /api/site/session        - a quick_demo pair session is created
  3. socket.io /site connect       - /sim/socket.io path works, HAL boots
  4. GET  /api/site/session/<id>   - session is tracked as started

Usage:
    python3 scripts/sim_smoke.py [BASE_URL]

    BASE_URL        default http://localhost:8080

Exit code is non-zero if any step fails (fail loud - suitable as a launch gate
and CI check). Run it against localhost and against the deployed backend.

Deps: requests, python-socketio[client]  (see scripts/requirements.txt)
"""

import argparse
import os
import sys
import time
from typing import NoReturn

# Running `python3 scripts/sim_smoke.py` puts scripts/ on sys.path[0], where
# scripts/inspect.py shadows the stdlib `inspect` (which requests imports).
# Drop our own dir so third-party imports resolve against stdlib.
_self_dir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _self_dir]

import requests

try:
    import socketio  # python-socketio client
except ImportError:
    sys.exit("Missing dep: pip install 'python-socketio[client]' (see scripts/requirements.txt)")


def _fail(step: str, detail: str) -> NoReturn:
    print(f"  FAIL  {step}: {detail}")
    print("\nSMOKE FAILED")
    sys.exit(1)


def _ok(step: str, detail: str = "") -> None:
    print(f"  OK    {step}{(' - ' + detail) if detail else ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default="http://localhost:8080")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Smoke target: {base}")

    # 1. Health -------------------------------------------------------------
    try:
        r = requests.get(f"{base}/healthz", timeout=10)
    except requests.RequestException as e:
        _fail("healthz", f"request error: {e}")
    if r.status_code != 200 or r.json().get("status") != "ok":
        _fail("healthz", f"got {r.status_code} {r.text!r}")
    _ok("healthz")

    # 2. Create site session ------------------------------------------------
    r = requests.post(
        f"{base}/api/site/session",
        json={"starter_mode": "quick_demo"},
        timeout=30,
    )
    if r.status_code != 201:
        _fail("create session", f"expected 201, got {r.status_code} {r.text!r}")
    body = r.json()
    session_id = body.get("session_id")
    if not session_id or body.get("status") != "started":
        _fail("create session", f"unexpected body {body!r}")
    _ok("create session", session_id)

    # 3. Socket.io /site connect -> session_ready ---------------------------
    sio = socketio.Client(reconnection=False)
    ready = {"got": False, "err": None}

    @sio.on("session_ready", namespace="/site")
    def _on_ready(_data):
        ready["got"] = True

    @sio.on("connect_error", namespace="/site")
    def _on_err(data):
        ready["err"] = str(data)

    conn_url = f"{base}?session_id={session_id}&role=alice"
    try:
        sio.connect(
            conn_url,
            namespaces=["/site"],
            # Server mounts the engine at /sim/socket.io, not the library
            # default /socket.io (see firmware/sim/README.md). polling+websocket
            # matches the actual frontend, which allows polling fallback.
            socketio_path="sim/socket.io",
            transports=["polling", "websocket"],
            wait=True,
            wait_timeout=15,
        )
    except Exception as e:
        _fail("socket connect", f"{e} (connect_error={ready['err']})")

    deadline = time.time() + 15
    while not ready["got"] and time.time() < deadline:
        sio.sleep(0.25)
    if not ready["got"]:
        sio.disconnect()
        _fail("socket session_ready", "no session_ready within 15s (HAL boot / websocket path)")
    _ok(f"socket connect + session_ready ({sio.transport()})")

    # 4. Session is tracked -------------------------------------------------
    r = requests.get(f"{base}/api/site/session/{session_id}", timeout=10)
    if r.status_code != 200 or r.json().get("status") != "started":
        sio.disconnect()
        _fail("get session", f"got {r.status_code} {r.text!r}")
    _ok("get session")
    sio.disconnect()  # frees the pair slot via disconnect-grace

    print("\nSMOKE PASSED")


if __name__ == "__main__":
    main()
