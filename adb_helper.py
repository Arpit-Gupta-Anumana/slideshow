#!/usr/bin/env python3
"""Unified ADB capture helper: shutter, rename, pull to local, batch rsync to remote.

Combines:
  - samsung_dell: sync capture, polling with early exit, device checks, audit log,
                  folder label + suffix naming, adb timeouts, sh_quote
  - moto_mac:     single worker queue, stability check, configurable poll/wait
  - pull_back:    pull-to-Mac, batch rsync, verbose per-step logging, /api/stats, /api/sync
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Phone paths ──────────────────────────────────────────────────────────────
CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"

# ── Capture timing ───────────────────────────────────────────────────────────
CAPTURE_MAX_WAIT = float(os.environ.get("SLIDESHOW_CAPTURE_MAX_WAIT", "8"))
CAPTURE_POLL = float(os.environ.get("SLIDESHOW_CAPTURE_POLL", "0.3"))
STABILITY_TICKS = int(os.environ.get("SLIDESHOW_STABILITY_TICKS", "2"))
ADB_TIMEOUT = int(os.environ.get("SLIDESHOW_ADB_TIMEOUT", "30"))

# ── Naming ───────────────────────────────────────────────────────────────────
FOLDER_LABEL = os.environ.get("SLIDESHOW_FOLDER_LABEL", "").strip()
CAPTURE_SUFFIX = os.environ.get("SLIDESHOW_CAPTURE_SUFFIX", "").strip()

# ── Sync vs async ────────────────────────────────────────────────────────────
SYNC_CAPTURE = os.environ.get("SLIDESHOW_SYNC_CAPTURE", "1").strip().lower() not in ("0", "false", "no")

# ── Local pull ───────────────────────────────────────────────────────────────
LOCAL_SAVE_DIR = os.environ.get("CAPTURE_DIR", "").strip()

# ── Remote rsync ─────────────────────────────────────────────────────────────
REMOTE_HOST = os.environ.get("REMOTE_HOST", "").strip()
REMOTE_PATH = os.environ.get("REMOTE_PATH", "").strip()
SSH_KEY = os.environ.get("SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1000"))

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = os.environ.get("LOG_DIR", LOCAL_SAVE_DIR or "/tmp")
AUDIT_LOG = os.environ.get("SLIDESHOW_CAPTURE_LOG", os.path.join(LOG_DIR, "capture_audit.jsonl"))

IMG_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng", ".bmp", ".gif", ".tiff"})

# ── Setup logging ────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("slideshow")
log.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(_fmt)
log.addHandler(_console)

_all_fh = logging.FileHandler(os.path.join(LOG_DIR, "slideshow.log"))
_all_fh.setLevel(logging.DEBUG)
_all_fh.setFormatter(_fmt)
log.addHandler(_all_fh)

_err_fh = logging.FileHandler(os.path.join(LOG_DIR, "slideshow_errors.log"))
_err_fh.setLevel(logging.WARNING)
_err_fh.setFormatter(_fmt)
log.addHandler(_err_fh)

# ── State ────────────────────────────────────────────────────────────────────
stats_lock = threading.Lock()
stats = {
    "triggered": 0, "captured": 0, "capture_failed": 0,
    "renamed": 0, "rename_failed": 0,
    "pulled": 0, "pull_failed": 0,
    "phone_cleaned": 0,
    "synced": 0, "sync_failed": 0,
}

_capture_q: queue.Queue[str] = queue.Queue()
_upload_lock = threading.Lock()


# ── Helpers ──────────────────────────────────────────────────────────────────

def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def run_cmd(cmd, label, timeout=None):
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    log.info(f"  CMD [{label}]: {cmd_str}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or ADB_TIMEOUT)
    except subprocess.TimeoutExpired:
        log.error(f"  CMD [{label}]: TIMEOUT after {ADB_TIMEOUT}s")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")

    elapsed = time.time() - t0
    log.info(f"  CMD [{label}]: exit={result.returncode} ({elapsed:.2f}s)")
    if result.stdout and result.stdout.strip():
        log.info(f"  CMD [{label}] stdout: {result.stdout.strip()}")
    if result.stderr and result.stderr.strip():
        lvl = logging.ERROR if result.returncode != 0 else logging.INFO
        log.log(lvl, f"  CMD [{label}] stderr: {result.stderr.strip()}")
    return result


def adb_available():
    return shutil.which("adb") is not None


def device_connected():
    if not adb_available():
        return False
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        lines = [l for l in r.stdout.strip().splitlines() if l and not l.startswith("List")]
        return any("device" in l and "unauthorized" not in l for l in lines)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_camera_image(name):
    return os.path.splitext(name)[1].lower() in IMG_EXTENSIONS


def camera_files():
    if not adb_available():
        return []
    try:
        r = subprocess.run(
            ["adb", "shell", f"ls -t {CAMERA_DIR}/ 2>/dev/null"],
            capture_output=True, text=True, timeout=15
        )
        return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def build_target_name(slideshow_name, captured_ext):
    base = os.path.splitext(slideshow_name)[0].replace("'", "_")
    ext = captured_ext or ".jpg"
    parts = [base]
    if FOLDER_LABEL:
        parts.append(FOLDER_LABEL)
    if CAPTURE_SUFFIX:
        parts.append(CAPTURE_SUFFIX)
    return "_".join(parts) + ext


def append_audit(payload: dict):
    try:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"audit log write failed: {e}")


# ── Rsync to remote ─────────────────────────────────────────────────────────

def sync_to_remote():
    if not REMOTE_HOST or not REMOTE_PATH or not LOCAL_SAVE_DIR:
        log.info("SYNC: skipped (REMOTE_HOST/REMOTE_PATH/CAPTURE_DIR not set)")
        return

    if not _upload_lock.acquire(blocking=False):
        log.info("SYNC: already in progress, skipping")
        return

    try:
        local_dir = LOCAL_SAVE_DIR.rstrip("/") + "/"
        file_count = len([f for f in os.listdir(local_dir)
                          if os.path.isfile(os.path.join(local_dir, f))
                          and not f.endswith((".log", ".jsonl"))])
        log.info(f"SYNC: starting — {file_count} files -> {REMOTE_HOST}:{REMOTE_PATH}")

        result = run_cmd(
            ["rsync", "-avz", "--remove-source-files",
             "-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no",
             local_dir, f"{REMOTE_HOST}:{REMOTE_PATH}"],
            "rsync", timeout=300
        )

        if result.returncode == 0:
            synced = [l for l in result.stdout.splitlines()
                      if not l.startswith(("sending", "sent", "total", "created")) and "." in l and l.strip()]
            with stats_lock:
                stats["synced"] += len(synced)
            log.info(f"SYNC: SUCCESS — {len(synced)} files uploaded")
            for f in synced:
                log.info(f"  SYNC file: {f.strip()}")
        else:
            with stats_lock:
                stats["sync_failed"] += 1
            log.error(f"SYNC: FAILED (exit {result.returncode})")
    except Exception as e:
        with stats_lock:
            stats["sync_failed"] += 1
        log.exception(f"SYNC: unexpected error: {e}")
    finally:
        _upload_lock.release()

    with stats_lock:
        log.info(f"STATS: {json.dumps(stats)}")


_pull_count = 0
_pull_count_lock = threading.Lock()

def maybe_trigger_sync():
    global _pull_count
    if not REMOTE_HOST or not REMOTE_PATH:
        return
    with _pull_count_lock:
        _pull_count += 1
        log.info(f"  BATCH: {_pull_count}/{BATCH_SIZE}")
        if _pull_count >= BATCH_SIZE:
            _pull_count = 0
            log.info(f"SYNC: batch of {BATCH_SIZE} reached, triggering upload...")
            threading.Thread(target=sync_to_remote, daemon=True).start()


# ── Core capture pipeline ────────────────────────────────────────────────────

def find_new_file_after_shutter(before_set: set[str]) -> str | None:
    """Poll camera dir until a new image appears and stabilizes."""
    deadline = time.time() + CAPTURE_MAX_WAIT
    stable_name = None
    stable_count = 0

    while time.time() < deadline:
        time.sleep(CAPTURE_POLL)
        current = camera_files()
        new_images = [n for n in current if n not in before_set and is_camera_image(n)]

        if not new_images:
            stable_name = None
            stable_count = 0
            continue

        newest = new_images[0]
        if len(new_images) > 1:
            log.info(f"  multiple new files; using newest: {newest} (also: {new_images[1:]})")

        if newest == stable_name:
            stable_count += 1
            if stable_count >= STABILITY_TICKS:
                return newest
        else:
            stable_name = newest
            stable_count = 1

    return stable_name if stable_name else None


def run_one_capture(target_name: str) -> dict:
    """Full pipeline for one slide. Returns result dict."""
    with stats_lock:
        stats["triggered"] += 1
        n = stats["triggered"]

    result = {"ok": False, "target": target_name, "src": None, "dest": None, "error": None, "n": n}

    log.info(f"{'='*60}")
    log.info(f"PHOTO #{n}: '{target_name}'")
    log.info(f"{'='*60}")

    # Pre-checks
    if not adb_available():
        result["error"] = "adb_not_found"
        log.error(f"  PHOTO #{n}: adb not found in PATH")
        append_audit(result)
        return result

    if not device_connected():
        result["error"] = "no_device"
        log.error(f"  PHOTO #{n}: no Android device connected")
        append_audit(result)
        return result

    # Step 1: snapshot camera dir before shutter
    log.info(f"  STEP 1/7: listing camera dir before shutter")
    before_set = set(camera_files())
    log.info(f"  {len(before_set)} existing files in camera dir")

    # Step 2: fire shutter
    log.info(f"  STEP 2/7: firing camera shutter (keyevent 27)")
    sh = run_cmd(["adb", "shell", "input", "keyevent", "27"], "shutter")
    if sh.returncode != 0:
        result["error"] = "shutter_failed"
        with stats_lock:
            stats["capture_failed"] += 1
        log.error(f"  PHOTO #{n}: shutter command failed")
        append_audit(result)
        return result

    # Step 3: poll for new file
    log.info(f"  STEP 3/7: polling for new photo (max {CAPTURE_MAX_WAIT}s, every {CAPTURE_POLL}s, {STABILITY_TICKS} stability ticks)")
    after = find_new_file_after_shutter(before_set)

    if not after:
        result["error"] = f"no_new_image_within_{CAPTURE_MAX_WAIT}s"
        with stats_lock:
            stats["capture_failed"] += 1
        log.error(f"  PHOTO #{n}: no new photo detected after {CAPTURE_MAX_WAIT}s")
        append_audit(result)
        return result

    with stats_lock:
        stats["captured"] += 1
    log.info(f"  CAPTURE OK: {after}")
    result["src"] = after

    # Step 4: build target name and rename on phone
    ext = os.path.splitext(after)[1]
    new_name = build_target_name(target_name, ext)
    phone_path = f"{RENAME_DIR}/{new_name}"
    result["dest"] = new_name

    log.info(f"  STEP 4/7: renaming {after} -> {phone_path}")
    run_cmd(["adb", "shell", f"mkdir -p {RENAME_DIR}"], "mkdir")

    mv = run_cmd(
        ["adb", "shell", f"mv {sh_quote(f'{CAMERA_DIR}/{after}')} {sh_quote(phone_path)}"],
        "rename"
    )
    if mv.returncode != 0:
        result["error"] = "mv_failed"
        with stats_lock:
            stats["rename_failed"] += 1
        log.error(f"  PHOTO #{n}: rename failed")
        append_audit(result)
        return result

    # Step 5: verify dest exists
    log.info(f"  STEP 5/7: verifying renamed file exists on phone")
    verify = run_cmd(
        ["adb", "shell", f"test -f {sh_quote(phone_path)} && echo ok"],
        "verify"
    )
    if "ok" not in (verify.stdout or ""):
        result["error"] = "dest_missing_after_mv"
        with stats_lock:
            stats["rename_failed"] += 1
        log.error(f"  PHOTO #{n}: file missing after mv")
        append_audit(result)
        return result

    with stats_lock:
        stats["renamed"] += 1
    log.info(f"  RENAME OK: {phone_path}")

    # Step 6: pull to Mac (if configured)
    if LOCAL_SAVE_DIR:
        os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
        local_path = os.path.join(LOCAL_SAVE_DIR, new_name)

        log.info(f"  STEP 6/7: pulling to Mac -> {local_path}")
        pull = run_cmd(["adb", "pull", phone_path, local_path], "pull")

        if pull.returncode == 0:
            with stats_lock:
                stats["pulled"] += 1
            log.info(f"  PULL OK: {local_path}")

            log.info(f"  cleaning phone: rm {phone_path}")
            rm = run_cmd(["adb", "shell", f"rm {sh_quote(phone_path)}"], "phone_rm")
            if rm.returncode == 0:
                with stats_lock:
                    stats["phone_cleaned"] += 1
                log.info(f"  PHONE CLEANED: {phone_path}")
            else:
                log.error(f"  PHONE CLEANUP FAILED: {phone_path}")

            # Step 7: maybe rsync
            log.info(f"  STEP 7/7: checking batch sync")
            maybe_trigger_sync()
        else:
            with stats_lock:
                stats["pull_failed"] += 1
            log.error(f"  PULL FAILED: keeping {phone_path} on phone")
    else:
        log.info(f"  STEP 6/7: pull to Mac skipped (CAPTURE_DIR not set)")
        log.info(f"  STEP 7/7: rsync skipped")

    result["ok"] = True
    log.info(f"  PHOTO #{n} COMPLETE: {target_name} -> {new_name}")
    with stats_lock:
        log.info(f"  STATS: {json.dumps(stats)}")
    append_audit(result)
    return result


# ── Worker queue (async mode) ────────────────────────────────────────────────

def _capture_worker():
    while True:
        target = _capture_q.get()
        try:
            run_one_capture(target)
        except Exception as e:
            log.exception(f"worker: uncaught error for {target}: {e}")
        finally:
            _capture_q.task_done()

threading.Thread(target=_capture_worker, daemon=True, name="adb-capture").start()


# ── HTTP handler ─────────────────────────────────────────────────────────────

_capture_lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/trigger":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                target_name = body.get("name", "unknown.jpg")
                log.info(f"HTTP: POST /trigger -> {target_name} (mode={'sync' if SYNC_CAPTURE else 'async'})")

                if SYNC_CAPTURE:
                    with _capture_lock:
                        res = run_one_capture(target_name)
                    self._send_json(200 if res["ok"] else 500, res)
                else:
                    _capture_q.put(target_name)
                    self._send_json(200, {"ok": True, "queued": True, "target": target_name})
            except Exception as e:
                log.exception(f"HTTP: trigger error: {e}")
                self._send_json(500, {"ok": False, "error": str(e)})

        elif self.path == "/sync":
            log.info("HTTP: POST /sync -> manual sync")
            threading.Thread(target=sync_to_remote, daemon=True).start()
            self._send_json(200, {"ok": True, "action": "sync_started"})

        elif self.path == "/stats":
            with stats_lock:
                self._send_json(200, dict(stats))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if LOCAL_SAVE_DIR:
        os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)

    server = HTTPServer(("127.0.0.1", 8900), Handler)

    log.info(f"ADB helper listening on 127.0.0.1:8900")
    log.info(f"Capture mode: {'sync (browser waits)' if SYNC_CAPTURE else 'async (queued)'}")
    log.info(f"Polling: every {CAPTURE_POLL}s, max wait {CAPTURE_MAX_WAIT}s, stability {STABILITY_TICKS} ticks")

    name_parts = ["<stem>"]
    if FOLDER_LABEL:
        name_parts.append(FOLDER_LABEL)
    if CAPTURE_SUFFIX:
        name_parts.append(CAPTURE_SUFFIX)
    log.info(f"Rename pattern: {'_'.join(name_parts)}<ext>")

    if LOCAL_SAVE_DIR:
        log.info(f"Pull to Mac: {os.path.abspath(LOCAL_SAVE_DIR)}")
    else:
        log.info(f"Pull to Mac: OFF (set CAPTURE_DIR to enable)")

    if REMOTE_HOST and REMOTE_PATH:
        log.info(f"Remote sync: every {BATCH_SIZE} files -> {REMOTE_HOST}:{REMOTE_PATH}")
    else:
        log.info(f"Remote sync: OFF (set REMOTE_HOST + REMOTE_PATH to enable)")

    if not adb_available():
        log.warning("adb not found in PATH — camera trigger will not work")
    elif not device_connected():
        log.warning("No Android device connected — camera trigger will not work until a device is connected")

    log.info(f"Logs: {os.path.abspath(LOG_DIR)}/slideshow.log | slideshow_errors.log")
    log.info(f"Audit: {os.path.abspath(AUDIT_LOG)}")

    server.serve_forever()
