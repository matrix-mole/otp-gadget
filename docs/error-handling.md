# Error handling and recovery

How the device handles errors. Two classes, handled differently. The security invariant: no crash or error path may leave the DEK in RAM or render any secret state.

## Error classes

- **Expected errors** — wrong PIN, malformed QR/hex input, message authentication failure. Handled inline on the relevant screen with a short, clear message; the user retries. No teardown.
- **Card removed or changed** — the own card lives in the internal TF slot but may be pulled or swapped at any time. The device checks card presence and mounts the filesystem fresh at every unlock attempt. Any own-card I/O error (e.g. `EIO` from a stale mount) routes to a clear *"Card removed or changed — reinsert and re-enter PIN"* screen, not a raw traceback. This path zeroes the DEK and returns to PIN entry.
- **Unexpected errors** — any otherwise-uncaught exception. A single global error boundary wraps the UI event loop. On trigger it performs the same teardown as auto-lock (zero the DEK, clear in-RAM message history), then shows the Recovery screen.

## Security invariant

No crash or error path may leave the DEK in RAM or render any secret state (message text, pad bytes, PIN digits). The Recovery screen shows only the error type and the screen breadcrumb below.

## Screen breadcrumb

The device keeps an in-RAM ring buffer of the last few screen identifiers visited (e.g. `PIN → Home → Send → Encode`). It records **screen names only** — never message content, contact names, PIN digits, or any secret — so it cannot leak metadata. Like message history it is RAM-only and cleared on power-off and lock.

## Recovery screen

Shows the error type and the screen breadcrumb, with an instruction to photograph the screen and email it for debugging. The device has no network by design, so sending is a manual off-device step (photo on a phone). The only action is `Return to PIN entry`.
