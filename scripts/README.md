# scripts/

Utility scripts for development and deployment.

## For AI agents

These scripts are designed so an AI assistant can drive the connected hardware end-to-end without the user being in the loop, as long as the gadgets are plugged in via USB-C. The typical agent workflow:

1. `./scripts/inspect.sh list` - see which boards are connected and their labels.
2. `./scripts/flash.sh --label <name>` - flash a specific board non-interactively.
3. `./scripts/inspect.sh <label> enable-debug` and `watchdog-on` - turn on debug mode (file-based touch injection) and the watchdog.
4. `./scripts/inspect.sh <label> state` - read post-freeze state: last screen breadcrumb, reset cause, free RAM, SD mount status.
5. `./scripts/inspect.sh <label> click X Y` / `type "..."` / `macro <name>` - drive the UI remotely.

The agent only needs the user for physical actions (plugging USB, swapping SD cards, long-pressing PWR when the watchdog is off). Interactive prompts are only used when no `--label` is given.

## Index

- `flash.sh` - deploy firmware to a connected board (see below)
- `reset.sh` - soft-reset all connected boards (or one with `--label <name>`)
- `inspect.sh` / `inspect.py` - post-freeze debug: read breadcrumb/reboot history, inject touches, replay macros (see below)
- `run.sh` - start the simulator locally
- `sim_smoke.py` - end-to-end backend smoke test for the live demo path
  (health, site session creation, websocket, session status). Launch gate; run
  vs localhost and the deployed backend: `python3 scripts/sim_smoke.py [BASE_URL]`
- `qr_size_check.py` - one-off spike to determine max plaintext size for QR
- `qr_size_check_results.md` - output of the spike
- `check_em_dashes.sh` - pre-commit hook: rejects em dashes in tracked files
- `cost_calculator.py` - prints a NOK-denominated cost breakdown (components, shipping, customs/VAT). Run with `python3 scripts/cost_calculator.py`. Not part of any release.
- `export_release.py` - export a public release folder or a paid Builder Pack zip from this private repo (see below)
- `release-manifest.public.yml` - whitelist for the public open-source release (firmware, parts list, order checklist, assembly, case docs, etc.)
- `release-manifest.builder-pack.yml` - whitelist for the paid Builder Pack zip (just the printable case `.3mf` files)
- `requirements.txt` - Python deps for the scripts venv
- `macros/` - named tap-sequence files for reproducing known bugs

## flash.sh

Deploys firmware source files to a connected RP2350 board via `mpremote`.

**Prerequisites:** `mpremote` must be installed (`pip install mpremote`).

**Usage:**

```bash
./scripts/flash.sh                       # interactive: pick a board from a list
./scripts/flash.sh --clean               # interactive, wipe firmware first
./scripts/flash.sh --label alice         # non-interactive: flash the board labeled "alice"
./scripts/flash.sh --label alice --clean # non-interactive, wipe firmware first
./scripts/flash.sh --all                 # non-interactive: flash every connected board
./scripts/flash.sh --all --clean         # non-interactive, all boards, wipe firmware first
./scripts/flash.sh --refresh-list        # force re-probing every connected board (ignore cache)
```

**Interactive mode** (no `--label` and no `--all`):

1. Lists all connected boards with their friendly label, firmware version hash, last-flashed timestamp, and an outdated indicator if the board's firmware doesn't match the local source.
2. Prompts you to pick one - type a board number, or `a` to flash all connected boards in sequence.
3. If the chosen board has no label yet, prompts for one and writes it to `/flash/device_label.txt`. (When flashing all, any unlabeled boards are prompted for in order.)

**Board listing cache:**

Probing a board with `mpremote` interrupts whatever firmware it's running (Ctrl-C → raw REPL → soft reset). To avoid disturbing idle boards every time you flash, listing data is cached at `~/.cache/otp-gadget-flash/board-cache.json`, keyed by serial port (`{port: {label, version, flashed_at}}`). On startup the script uses the cache for any connected port it already knows about; only unknown ports are probed live. The cache entry for a board is refreshed automatically after each successful flash of that board. Pass `--refresh-list` to discard the cache for all currently connected ports and re-probe every board (use this if you suspect the cache is stale, e.g. you flashed a board from another machine).

**Upload retry:**

If an `mpremote` call fails with a transient USB CDC error (e.g. "Device not configured", or "failed to access … it may be in use by another program"), the script retries the entire invocation up to 3 times with exponential backoff (2 s, then 4 s) so macOS has time to re-enumerate the device and release the previous file descriptor. If all 3 attempts fail, the script exits with the original error.

Between boards (when flashing several in one run), the script also waits 5 s after resetting the previous board before touching the next one - the post-reset USB churn can otherwise perturb the next board's CDC endpoint mid-upload.

**Non-interactive mode** (`--label <name>` or `--all`):

- `--label <name>`: finds the connected board whose `/flash/device_label.txt` matches `<name>` and flashes it directly. Errors out if no connected board has that label, or if the matching board is unlabeled.
- `--all`: flashes every connected board in sequence, in the order returned by `mpremote devs`. Errors out if any connected board is unlabeled (assign labels interactively first). `--label` and `--all` are mutually exclusive.
4. **Normal flash:** computes SHA-256 of each local source file. Reads the board's firmware manifest (`/flash/firmware_manifest.json`) to determine what has changed. Uploads only new or modified files, deletes files present on the board but removed from source. Skips the upload entirely if nothing changed.
5. **`--clean` flash:** wipes `/firmware/` entirely first, then uploads all files regardless of the manifest. Use this when you suspect the board is in a bad state or want a guaranteed clean slate. `/flash/` is always preserved (device label, `device_secret`, manifest).
6. Writes `/flash/firmware_manifest.json` **after** a successful upload (so a crashed upload never leaves a stale manifest). The manifest stores: per-file SHA-256 hash, an aggregate version hash (SHA-256 of the sorted per-file hashes), and a last-flashed UTC timestamp.
7. Resets the board so `main.py` starts running.

**Board listing example:**

```
Scanning for connected boards...
Local firmware: a1b2c3d

  1) alice   /dev/cu.usbmodem1101   a1b2c3d ✓  2026-05-11 14:32 UTC
  2) bob     /dev/cu.usbmodem1201   9f8e7d6 ⚠  2026-05-10 09:15 UTC  (outdated)
  3) carol   /dev/cu.usbmodem1301   (no manifest)
```

**What gets uploaded:**

| Path on laptop | Destination on board | Notes |
|---|---|---|
| `main.py` | `/main.py` | entry point |
| `firmware/__init__.py` | `/firmware/__init__.py` | package marker |
| `firmware/core/` | `/firmware/core/` | all business logic |
| `firmware/hal/__init__.py` | `/firmware/hal/__init__.py` | package marker |
| `firmware/hal/base.py` | `/firmware/hal/base.py` | HAL interface |
| `firmware/hal/real.py` | `/firmware/hal/real.py` | real hardware impl |
| `firmware/hal/drivers/` | `/firmware/hal/drivers/` | vendored MicroPython drivers |

The board mirrors the repo's `firmware/` package structure so all `from firmware.xxx import yyy` imports resolve the same way on the board as they do in the simulator.

**What is excluded:**

- `firmware/hal/sim.py` - simulator only, not needed on board
- `firmware/sim/` - Flask web app, not needed on board
- `firmware/tests/` - run on laptop, not on board
- `firmware/setup/` - one-time setup scripts, run manually if needed
- All `README.md` files

**Device labels:**

Each board stores its label in `/flash/device_label.txt` (e.g. `alice`, `bob`). The script preserves everything under `/flash/` across re-deploys (including `--clean`), so the label and `device_secret` survive. If the board's MicroPython is re-flashed via `.uf2` drag-and-drop, the label is lost and the script will prompt for it again on the next run.

**Firmware manifest (`/flash/firmware_manifest.json`):**

Written to the board after every successful flash. Contains:
- `version`: aggregate SHA-256 hash (hex) of all uploaded file hashes - the "firmware version"
- `flashed_at`: UTC timestamp of the last successful flash (ISO 8601)
- `files`: object mapping each file path to its SHA-256 content hash

The manifest is used on the next incremental flash to skip unchanged files. It is preserved across `--clean` flashes (written fresh after each run). If it is missing or corrupt, the script falls back to uploading all files.

**Multi-board workflow:**

Connect both boards and either run `./scripts/flash.sh --all` (non-interactive) or `./scripts/flash.sh` and pick `a` at the prompt. Boards are flashed one after another with a single invocation.

## reset.sh

Soft-resets all connected boards in one command. Useful after debugging sessions or when you want a clean boot without re-flashing.

**Usage:**

```bash
./scripts/reset.sh                  # reset all connected boards
./scripts/reset.sh --label alice    # reset only the board labeled "alice"
```

## inspect.sh

Diagnose post-freeze state and drive the UI remotely (debug mode only). Requires `mpremote` (same venv as `flash.sh`).

**Usage:**

```bash
./scripts/inspect.sh list                        # list connected boards (label, port, firmware version)
./scripts/inspect.sh <label> state               # one-shot snapshot of all state fields
./scripts/inspect.sh <label> click X Y           # inject one synthetic tap
./scripts/inspect.sh <label> type "hello"        # type via keyboard (flag --kb qwerty|digits|hex)
./scripts/inspect.sh <label> macro <name>        # replay scripts/macros/<name>.txt
./scripts/inspect.sh <label> enable-debug        # create /flash/debug_mode.txt + reboot
./scripts/inspect.sh <label> disable-debug       # delete /flash/debug_mode.txt + reboot
./scripts/inspect.sh <label> watchdog-on         # create /flash/watchdog.txt + reboot
./scripts/inspect.sh <label> watchdog-off        # delete /flash/watchdog.txt + reboot
./scripts/inspect.sh <label> reboot              # soft reset
./scripts/inspect.sh <label> tail-prints         # stream stdout from running script
```

**`state` output example:**

```
== alice (/dev/cu.usbmodem1101) ==
Last boot:        2026-05-11 14:32:17 UTC (cause: WDT)
Boot history:     3× WDT, 1× POWER_ON in last 4 boots
Last breadcrumb:  PrepareExchange.write_X_own@4194304/10485760
                  (2 s before last boot)
Recent trail:     (last 10 from /flash/breadcrumb_ring.txt)
                    PINEntry
                    Home
                    Contacts
                    PrepareExchange
                    PrepareExchange.write_X_own@524288/10485760
                    PrepareExchange.write_X_own@1048576/10485760
                    PrepareExchange.write_X_own@1572864/10485760
                    PrepareExchange.write_X_own@2097152/10485760
                    PrepareExchange.write_X_own@3145728/10485760
                    PrepareExchange.write_X_own@4194304/10485760
Live state:       responsive
Current screen:   Home (from /flash/last_state.txt, fresh)
I2C devices:      0x15 (touch), 0x34 (axp), 0x51 (rtc)  [all expected]
SD own:           MOUNTED
SD guest:         NO_CARD
Free RAM:         312 KB
Debug mode:       ON
Watchdog:         ENABLED (8 s)
```

**`list` output example:**

```
alice    /dev/cu.usbmodem1101    a1b2c3d  ✓  2026-05-11 14:32 UTC
bob      /dev/cu.usbmodem1201    9f8e7d6  ⚠  2026-05-10 09:15 UTC  (outdated)
[unlabeled]  /dev/cu.usbmodem1301    (no manifest)
```

**Touch injection** works only when debug mode is on (`/flash/debug_mode.txt` exists). The firmware polls `/flash/inject_queue.txt` on every `get_touch()` call; `inspect.sh click X Y` appends a line to that file. No mpremote exec interruption of the main loop. See `firmware/README.md` → `## Debug Tooling` for full details.

**Macros** live at `scripts/macros/<name>.txt` - one `click X Y`, `type "..."`, or `sleep N` per line. Comments start with `#`.

```
# Example: scripts/macros/freeze-prepare-exchange.txt
click 40 340          # Contacts
sleep 0.5
click 40 220          # + Add contact
```

## export_release.py

Exports a subset of this private repo for distribution. Two targets:

- `--target public` writes a folder intended to be the working tree of the public open-source repo (`matrix-mole/otp-gadget` or equivalent). Includes firmware, parts list, order checklist, assembly docs, case support docs (slicer settings, before-print checklist) — everything a builder needs except the printable case CAD.
- `--target builder-pack` writes a zip for upload to Gumroad as the paid Builder Pack. Contains just the printable case `.3mf` files.

Each target is driven by its own manifest: `release-manifest.public.yml` / `release-manifest.builder-pack.yml`. See [`docs/commercialization/open-source-release-strategy.md`](../docs/commercialization/open-source-release-strategy.md) for what belongs in each list and why.

**Prerequisites:**

```bash
python3 -m venv scripts/venv && source scripts/venv/bin/activate
pip install -r scripts/requirements.txt
```

**Usage:**

```bash
python scripts/export_release.py --target public                # writes ../otp-gadget/
python scripts/export_release.py --target builder-pack          # writes ../otp-gadget-builder-pack.zip
python scripts/export_release.py --target public --dry-run      # list files, write nothing
python scripts/export_release.py --target public --force        # overwrite an existing output
```

**Pipeline:**

1. Read the manifest (`release-manifest.<target>.yml`).
2. Walk the repo from root, skipping `.git/` and `node_modules/`.
3. Keep files that match an `include` pattern and don't match any `exclude` pattern (gitignore-style globs via `pathspec`).
4. Run a secret scan on each included file: built-in patterns (GitHub/Google/AWS/Stripe keys, private-key headers, generic `secret = "..."` / `password = "..."` / `api_key = "..."` assignments) plus any `extra_secret_patterns` from the manifest. Binary files are skipped. Files listed in the manifest's `allowlist` may match without aborting.
5. On any unallowlisted match, the script exits with a list of offending files (no output written).
6. For each entry in the manifest's `render_html` list, render the source markdown to a self-contained HTML file (inline CSS, no external assets) and ship it next to the source. Available for any future paid pack that wants to bundle a polished HTML guide (the current Builder Pack is just `.3mf` files, so no markdown rendering happens today).
7. On success: folder mode copies files preserving paths (existing `.git/` in the target is preserved by `--force`, the rest is wiped); zip mode writes a single archive.

The script is idempotent. A non-empty folder target or pre-existing zip refuses to overwrite without `--force`, so accidental re-runs can't silently destroy local edits.
