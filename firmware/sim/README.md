# Simulator (`firmware/sim/`)

Flask web app that runs the real `core/` logic on a laptop, rendering the 320×480 screen in a browser. Used for development and as the live demo backend for the marketing site.

## Running locally

```bash
./dev.sh
```

`dev.sh` creates `.env` from `.env.example` on first run, loads it, then
launches the sim at `http://127.0.0.1:8080`. No env prefix needed, and no
database or Docker required - the sim is fully in-memory. The sim requires
`ALLOWED_ORIGINS`, `MAX_CONCURRENT_PAIRS`, `SESSION_IDLE_TIMEOUT_SECONDS`,
`SESSION_MAX_LIFETIME_SECONDS` (all asserted at startup; see `.env.example`).

## Index

- `app.py` - Flask + SocketIO server; admin hub (named persistent instances) + site session layer (see below)
- `templates/` - Jinja2 templates for the admin hub UI

## Admin hub

Named, persistent device instances used for manual development and testing. Accessible at `http://localhost:8080`. See `firmware/README.md → Simulator` for full docs.

## Deployment & origins

This Flask app is **backend-only**. The marketing/demo frontend lives in the
`matrix-mole` repo (Next.js) and is served by Vercel at
**`matrixmole.com/otp-gadget`**. This service is deployed separately on
Coolify/VPS at the backend-only subdomain
**`otp-gadget-sim.matrixmole.com`**:

- `/sim/*` → SocketIO WebSocket + HTTP
- `/api/*` → sim REST (site session lifecycle)

No static frontend is served here. (The old single-subdomain
`otp.matrixmole.com` setup is retired.)

**Socket.IO path:** the SocketIO engine endpoint is served at
**`/sim/socket.io`**, not the library default `/socket.io`. There is no proxy
path rewrite (see `plans/coolify-dns-runbook.md`), so the server path must
match the frontend's `NEXT_PUBLIC_OTP_SIM_URL` pathname (`.../sim`) →
`/sim/socket.io` in both local dev and production. The admin hub client uses
the same path.

Because the frontend is a different origin, `ALLOWED_ORIGINS` (asserted at
startup, comma-separated, no default) must list every frontend origin:

```
https://matrixmole.com,https://www.matrixmole.com,https://staging.matrixmole.com,http://localhost:3000
```

- `https://staging.matrixmole.com` - the permanent site-wide staging
  environment (Vercel domain bound to the long-lived `staging` branch). A
  stable alias is used instead of a `*.vercel.app` regex so flask-cors /
  Flask-SocketIO match an explicit origin.
- `http://localhost:3000` - Next dev server.

(The pre-migration values `https://otp.matrixmole.com` + `http://localhost:5173`
no longer apply.)

## Production runtime & build

Local dev uses the Werkzeug dev server (`./dev.sh`). **Production must not** -
it cannot hold the concurrent WebSocket load (up to `MAX_CONCURRENT_PAIRS` × 2
sockets). Production runs under **gunicorn with a single gevent-websocket
worker** (the long-standing Flask-SocketIO recipe; eventlet is deprecated
upstream). SocketIO requires sticky sessions, so the service runs one worker
and scales by container resources, not worker count:

```
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
         -w 1 -b 0.0.0.0:$PORT firmware.sim.wsgi:app
```

- Binds `0.0.0.0` and honors Coolify's injected `$PORT` (the dev server's
  hardcoded `127.0.0.1:8080` is dev-only).
- The production path skips the dev-only interactive "kill port" prompt and the
  watchdog hot-reloader in `firmware/sim/__main__.py`.
- One-time startup work (stale-lock/session cleanup, `quick_demo` fixture bake)
  runs at app creation, before the first request.

**Build:** a `Dockerfile` at the **repo root** (build context = repo root, not
`firmware/sim/`) - the sim imports the `firmware` package and reads
`firmware/sim/fixtures` by relative path, so the whole `firmware/` tree must be
in the image. Installs `firmware/requirements.txt`. Coolify builds this
Dockerfile and auto-deploys on push to `main`.

**Health check:** `GET /healthz` returns `200 {"status":"ok"}` without touching
session state, for Coolify's container health probe. (`/` is the admin hub and
is not a health endpoint.) Coolify runs this probe with `curl` *inside* the
container, so the image installs `curl` (the `python:3.12-slim` base ships
without it; without `curl`/`wget` the probe always fails and Coolify rolls the
deploy back even though gunicorn is healthy).

**Rate limiter storage:** `flask-limiter` intentionally uses its default
in-memory storage for launch. Production runs one gunicorn worker/process, so
the counters are shared for all live requests in that process. They reset on
restart/redeploy and are not suitable for multiple workers/containers, but the
limited route is low-stakes:

- `POST /api/site/session` - `10 per hour` per client IP, protects simulator
  session creation from casual abuse and resource churn.

Do not add Postgres/Redis-backed limiter storage yet. Revisit if production
uses more than one worker/container, if redeploy-cleared counters become a real
abuse path, or if these endpoints become higher stakes.

**Production env vars** (set in Coolify's env UI, never committed - all asserted
at startup, no defaults):

| Var | Production value |
|---|---|
| `ALLOWED_ORIGINS` | the four frontend origins listed above |
| `MAX_CONCURRENT_PAIRS` | tuned against the VPS once Try-it works end-to-end |
| `SESSION_IDLE_TIMEOUT_SECONDS` | `300` |
| `SESSION_MAX_LIFETIME_SECONDS` | `600` |

`.env.example` mirrors these keys for local dev only with placeholder values.

**Removed endpoint:** `POST /api/kit-interest` was deleted when the website
interest form was removed. Do not keep dead form/table/DB plumbing in the sim.

**Smoke test:** `scripts/sim_smoke.py <base-url>` exercises the live demo path:
health check → create `quick_demo` site session (REST) → SocketIO WebSocket
connect → session status check. Run against `localhost` and against the
deployed backend as the launch gate.

**CI is intentionally deferred.** Coolify auto-deploys on push but its health
check only probes `/healthz` - it does not exercise Socket.IO/CORS/session, so a
deploy can be green yet functionally broken. Until backend changes become
frequent, the gate is manual: run `scripts/sim_smoke.py <deployed-url>` once
after any backend change. Revisit CI (whitelisted runner IP, since
`POST /api/site/session` is rate-limited to 10/hr per IP) if that cadence picks
up post-launch.

## Site session layer

A separate namespace alongside the admin hub, added for the
`matrixmole.com/otp-gadget` marketing/demo page. Two device instances (Alice +
Bob) per visitor session, destroyed when the session ends.

The website Try-it tutorials are tightly coupled to this simulator and the
firmware UI flow. If a gadget screen, button label, contact-exchange step,
message step, or sim-only `notify_screen` name changes, update the website's
`apps/website/app/otp-gadget/_lib/ui/README.md` and `try-it.ts` in
`matrix-mole` in the same change.

See `site/README.md → Simulator integration` for the full spec. Implementation tracked in `site/CHECKLIST.md` section 5a.

## Sim-only divergences from real hardware

These are intentional differences that only affect simulated sessions. Real-hardware behavior (`firmware/hal/real.py`) is unchanged.

### `preunlocked` HAL flag (SimHAL only)

**Purpose:** site demo sessions skip PIN entry entirely - gadgets boot directly to Home.

**How it works:**

`SimHAL` accepts `preunlocked=True`. When set:

1. A fixed dummy DEK (`\x00 * 32`) is injected into RAM at construction - `hal._dek` is set immediately.
2. `unlock_secrets()` and `lock_secrets()` become no-ops, keeping the dummy DEK in RAM for the lifetime of the session.
3. All secret I/O (`read_secret`, `write_secret`, `read_secret_slice`, `read_secret_stream`, `write_secret_stream`) skips AES entirely - files under `/secret/` are stored as plaintext on disk.
4. At construction, two stub files are auto-created if missing so `boot.py`'s checks pass without running DeviceSetup or CardInit:
   - `<state_dir>/mcu_flash/device_secret.bin` - dummy 32 bytes
   - `<own_card_path>/secret/verify.bin` - raw `VERIFY_MAGIC + _PREUNLOCKED_DUMMY` (no AES wrapper)

`boot.py` calls `_pin_entry(hal)` instead of `PINEntryScreen(hal).run()` directly. `_pin_entry` checks `getattr(hal, '_preunlocked', False)` and returns immediately when set, so PIN entry is a no-op for preunlocked sessions. `getattr` defaults to `False` for any HAL without the attribute (including `RealHAL`), so real hardware is unaffected.

**Security note:** this flag exists only in `SimHAL`, which runs only on a laptop. `RealHAL` has no `_preunlocked` attribute and is completely unmodified. Preunlocked sessions are never used outside the simulator.
