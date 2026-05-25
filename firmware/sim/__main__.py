import os
import signal
import socket
import subprocess
import sys
import time

from firmware.sim.app import run


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pid_on_port(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        return int(pids[0]) if pids else None
    except Exception:
        return None


def _maybe_kill_port(port: int) -> None:
    if not _port_in_use(port):
        return
    pid = _pid_on_port(port)
    pid_str = f" (PID {pid})" if pid else ""
    answer = input(f"Port {port} is already in use{pid_str}. Kill it? [y/N] ").strip().lower()
    if answer == "y":
        if pid:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
        else:
            print("Could not find PID - kill it manually and retry.")
            sys.exit(1)
    else:
        sys.exit(1)


def _start_reloader(watch_path: str) -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("[sim] watchdog not installed - hot-reload disabled (pip install watchdog)")
        return

    class _Handler(FileSystemEventHandler):
        def __init__(self):
            self._triggered = False

        def on_any_event(self, event):
            if self._triggered or event.is_directory:
                return
            p = event.src_path
            if "__pycache__" in p:
                return
            if p.endswith((".py", ".html")):
                self._triggered = True
                print(f"\n[sim] {os.path.basename(p)} changed - restarting...\n")
                time.sleep(0.2)
                subprocess.Popen([sys.executable, "-m", "firmware.sim"])
                os._exit(0)

    observer = Observer()
    observer.schedule(_Handler(), path=watch_path, recursive=True)
    observer.daemon = True
    observer.start()


_firmware_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_start_reloader(_firmware_dir)

_maybe_kill_port(8080)
run()
