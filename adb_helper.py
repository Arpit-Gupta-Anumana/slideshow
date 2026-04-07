#!/usr/bin/env python3
"""ADB shutter + rename. One capture at a time (queue) so 15k slides do not spawn 15k threads."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"

CAPTURE_POLL_SEC = float(os.environ.get("SLIDESHOW_CAPTURE_POLL", "0.45"))
CAPTURE_MAX_WAIT_SEC = float(os.environ.get("SLIDESHOW_CAPTURE_MAX_WAIT", "28"))
ADB_TIMEOUT = int(os.environ.get("SLIDESHOW_ADB_SHELL_TIMEOUT", "60"))

_capture_q: queue.Queue[str] = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False


def _capture_suffix():
    """Appended as _<suffix> before the extension (default: 16k_moto_linear_dell). Set empty to disable."""
    return os.environ.get("SLIDESHOW_CAPTURE_SUFFIX", "16k_moto_linear_dell").strip()


def _adb_shell(cmd: str) -> str:
    try:
        r = subprocess.run(
            ["adb", "shell", cmd],
            capture_output=True,
            text=True,
            timeout=ADB_TIMEOUT,
        )
        return (r.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[warn] adb shell: {e}")
        return ""


def get_latest_photo():
    out = _adb_shell(f"ls -t {CAMERA_DIR}/ 2>/dev/null | head -n 1")
    return out if out else None


def _wait_for_new_file(before: str | None) -> str | None:
    deadline = time.monotonic() + CAPTURE_MAX_WAIT_SEC
    stable_name = None
    stable_ticks = 0
    while time.monotonic() < deadline:
        time.sleep(CAPTURE_POLL_SEC)
        after = get_latest_photo()
        if not after:
            continue
        if before and after == before:
            continue
        if after == stable_name:
            stable_ticks += 1
            if stable_ticks >= 2:
                return after
        else:
            stable_name = after
            stable_ticks = 1
    return None


def _do_capture_and_rename(target_name: str) -> None:
    before = get_latest_photo()

    r = subprocess.run(
        ["adb", "shell", "input", "keyevent", "27"],
        capture_output=True,
        text=True,
        timeout=ADB_TIMEOUT,
    )
    if r.returncode != 0:
        print(f"[warn] keyevent failed for {target_name}: {r.stderr or r.stdout}")
        return

    after = _wait_for_new_file(before)
    if not after:
        print(
            f"[warn] No new photo detected for: {target_name} "
            f"(waited up to {CAPTURE_MAX_WAIT_SEC}s)"
        )
        return

    ext = os.path.splitext(after)[1]
    base = os.path.splitext(target_name)[0]
    suf = _capture_suffix()
    new_name = f"{base}_{suf}{ext}" if suf else base + ext

    subprocess.run(
        ["adb", "shell", f"mkdir -p {RENAME_DIR}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=ADB_TIMEOUT,
    )

    def sh_quote(s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"

    mv = subprocess.run(
        [
            "adb",
            "shell",
            f"mv {sh_quote(f'{CAMERA_DIR}/{after}')} {sh_quote(f'{RENAME_DIR}/{new_name}')}",
        ],
        capture_output=True,
        text=True,
        timeout=ADB_TIMEOUT,
    )
    if mv.returncode != 0:
        print(f"[warn] mv failed for {target_name}: {mv.stderr or mv.stdout}")
        return
    print(f"[ok] {after} -> {RENAME_DIR}/{new_name}")


def _capture_worker() -> None:
    while True:
        target_name = _capture_q.get()
        try:
            _do_capture_and_rename(target_name)
        except Exception as e:
            print(f"[error] capture {target_name!r}: {e}")
        finally:
            _capture_q.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_capture_worker, daemon=True, name="adb-capture")
        t.start()
        _worker_started = True


def capture_and_rename(target_name: str) -> None:
    """Enqueue; processed strictly in order, one shutter at a time."""
    _ensure_worker()
    _capture_q.put(target_name)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/trigger":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                target_name = body.get("name", "unknown.jpg")
                capture_and_rename(target_name)
                self.send_response(200)
            except Exception as e:
                print(f"[error] {e}")
                self.send_response(500)
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8900), Handler)
    print("ADB helper listening on 127.0.0.1:8900")
    print(f"Renamed photos will go to: {RENAME_DIR}/")
    print(
        f"Queue capture: poll {CAPTURE_POLL_SEC}s, max wait {CAPTURE_MAX_WAIT_SEC}s/slide"
    )
    server.serve_forever()
