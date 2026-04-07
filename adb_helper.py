#!/usr/bin/env python3
"""Tiny HTTP server: triggers camera shutter via ADB, renames, pulls to Mac,
uploads to remote server in batches, cleans up phone and local storage."""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"
CAPTURE_WAIT = 4

LOCAL_SAVE_DIR = os.environ.get("CAPTURE_DIR", "./captured")
LOG_DIR = os.environ.get("LOG_DIR", LOCAL_SAVE_DIR)

REMOTE_HOST = os.environ.get("REMOTE_HOST", "arpit.gupta@dev3.nferx.com")
REMOTE_PATH = os.environ.get("REMOTE_PATH", "/dev3-datastore/arpit/LEF/")
SSH_KEY = os.environ.get("SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1000"))

pull_count = 0
pull_count_lock = threading.Lock()
upload_in_progress = threading.Lock()

# --- Logging setup ---
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("slideshow")
log.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.INFO)
console.setFormatter(fmt)
log.addHandler(console)

all_log = logging.FileHandler(os.path.join(LOG_DIR, "slideshow.log"))
all_log.setLevel(logging.DEBUG)
all_log.setFormatter(fmt)
log.addHandler(all_log)

err_log = logging.FileHandler(os.path.join(LOG_DIR, "slideshow_errors.log"))
err_log.setLevel(logging.WARNING)
err_log.setFormatter(fmt)
log.addHandler(err_log)

# --- Stats ---
stats_lock = threading.Lock()
stats = {"triggered": 0, "captured": 0, "capture_failed": 0,
         "pulled": 0, "pull_failed": 0, "synced": 0, "sync_failed": 0,
         "renamed": 0, "rename_failed": 0, "phone_cleaned": 0}


def run_cmd(cmd, description, capture=True):
    """Run a command, log it fully, return the CompletedProcess."""
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    log.info(f"  CMD [{description}]: {cmd_str}")
    t0 = time.time()

    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result.stdout = ""
        result.stderr = ""

    elapsed = time.time() - t0
    log.info(f"  CMD [{description}]: exit={result.returncode} ({elapsed:.2f}s)")

    if result.stdout and result.stdout.strip():
        log.info(f"  CMD [{description}] stdout: {result.stdout.strip()}")
    if result.stderr and result.stderr.strip():
        level = logging.ERROR if result.returncode != 0 else logging.INFO
        log.log(level, f"  CMD [{description}] stderr: {result.stderr.strip()}")

    return result


def get_latest_photo():
    result = run_cmd(
        ["adb", "shell", f"ls -t {CAMERA_DIR}/ | head -1"],
        "get_latest_photo"
    )
    name = result.stdout.strip()
    return name if name else None


def sync_to_remote():
    if not upload_in_progress.acquire(blocking=False):
        log.info("SYNC: upload already in progress, skipping")
        return

    try:
        local_dir = LOCAL_SAVE_DIR.rstrip("/") + "/"
        file_count = len([f for f in os.listdir(local_dir)
                          if os.path.isfile(os.path.join(local_dir, f)) and not f.endswith(".log")])
        log.info(f"SYNC: starting — {file_count} files in {local_dir} -> {REMOTE_HOST}:{REMOTE_PATH}")

        result = run_cmd(
            [
                "rsync", "-avz", "--remove-source-files",
                "-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no",
                local_dir,
                f"{REMOTE_HOST}:{REMOTE_PATH}"
            ],
            "rsync"
        )

        if result.returncode == 0:
            synced = [l for l in result.stdout.splitlines()
                      if not l.startswith(("sending", "sent", "total", "created")) and "." in l and l.strip()]
            with stats_lock:
                stats["synced"] += len(synced)
            log.info(f"SYNC: SUCCESS — uploaded {len(synced)} files")
            for f in synced:
                log.info(f"  SYNC file: {f.strip()}")
            with stats_lock:
                log.info(f"STATS: {json.dumps(stats)}")
        else:
            with stats_lock:
                stats["sync_failed"] += 1
            log.error(f"SYNC: FAILED (exit {result.returncode})")
    except Exception as e:
        with stats_lock:
            stats["sync_failed"] += 1
        log.exception(f"SYNC: unexpected error: {e}")
    finally:
        upload_in_progress.release()


def maybe_trigger_sync():
    global pull_count
    with pull_count_lock:
        pull_count += 1
        log.info(f"  BATCH: {pull_count}/{BATCH_SIZE}")
        if pull_count >= BATCH_SIZE:
            pull_count = 0
            log.info(f"SYNC: batch of {BATCH_SIZE} reached, triggering upload...")
            threading.Thread(target=sync_to_remote, daemon=True).start()


def capture_rename_pull(target_name):
    with stats_lock:
        stats["triggered"] += 1
        n = stats["triggered"]

    log.info(f"{'='*60}")
    log.info(f"PHOTO #{n}: '{target_name}'")
    log.info(f"{'='*60}")

    # Step 1: check what's on the phone before shutter
    log.info(f"  STEP 1/6: checking latest photo before shutter")
    before = get_latest_photo()
    log.info(f"  latest before: {before or '(none)'}")

    # Step 2: fire shutter
    log.info(f"  STEP 2/6: firing camera shutter (keyevent 27)")
    result = run_cmd(["adb", "shell", "input", "keyevent", "27"], "shutter")
    if result.returncode != 0:
        log.error(f"  STEP 2/6: SHUTTER FAILED")

    # Step 3: wait for camera
    log.info(f"  STEP 3/6: waiting {CAPTURE_WAIT}s for camera to save...")
    time.sleep(CAPTURE_WAIT)

    # Step 4: check what appeared
    log.info(f"  STEP 4/6: checking latest photo after shutter")
    after = get_latest_photo()
    log.info(f"  latest after: {after or '(none)'}")

    if not after or after == before:
        with stats_lock:
            stats["capture_failed"] += 1
        log.error(f"  PHOTO #{n} FAILED: no new photo detected (before={before}, after={after})")
        log.info(f"  STATS: {json.dumps(stats)}")
        return

    with stats_lock:
        stats["captured"] += 1
    log.info(f"  CAPTURE OK: new photo = {after}")

    # Step 5: rename on phone
    ext = os.path.splitext(after)[1]
    base = os.path.splitext(target_name)[0]
    new_name = base + ext
    phone_path = f"{RENAME_DIR}/{new_name}"

    log.info(f"  STEP 5/6: renaming on phone: {after} -> {phone_path}")

    run_cmd(["adb", "shell", f"mkdir -p {RENAME_DIR}"], "mkdir")

    mv_result = run_cmd(
        ["adb", "shell", f"mv '{CAMERA_DIR}/{after}' '{phone_path}'"],
        "rename"
    )
    if mv_result.returncode != 0:
        with stats_lock:
            stats["rename_failed"] += 1
        log.error(f"  RENAME FAILED for photo #{n}: {after}")
        return

    with stats_lock:
        stats["renamed"] += 1
    log.info(f"  RENAME OK: {phone_path}")

    # Step 6: pull to mac and clean phone
    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
    local_path = os.path.join(LOCAL_SAVE_DIR, new_name)

    log.info(f"  STEP 6/6: pulling to Mac: {phone_path} -> {local_path}")

    pull = run_cmd(["adb", "pull", phone_path, local_path], "pull")

    if pull.returncode == 0:
        with stats_lock:
            stats["pulled"] += 1
        log.info(f"  PULL OK: {local_path}")

        log.info(f"  cleaning phone: rm {phone_path}")
        rm_result = run_cmd(["adb", "shell", f"rm '{phone_path}'"], "phone_rm")
        if rm_result.returncode == 0:
            with stats_lock:
                stats["phone_cleaned"] += 1
            log.info(f"  PHONE CLEANED: {phone_path} deleted")
        else:
            log.error(f"  PHONE CLEANUP FAILED: could not delete {phone_path}")

        log.info(f"  PHOTO #{n} COMPLETE: {target_name} -> {local_path}")
        with stats_lock:
            log.info(f"  STATS: {json.dumps(stats)}")
        maybe_trigger_sync()
    else:
        with stats_lock:
            stats["pull_failed"] += 1
        log.error(f"  PULL FAILED for photo #{n}: {phone_path} — keeping on phone")
        with stats_lock:
            log.info(f"  STATS: {json.dumps(stats)}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/trigger":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                target_name = body.get("name", "unknown.jpg")
                log.info(f"HTTP: POST /trigger -> {target_name}")
                threading.Thread(target=capture_rename_pull, args=(target_name,), daemon=True).start()
                self.send_response(200)
            except Exception as e:
                log.exception(f"HTTP: trigger error: {e}")
                self.send_response(500)
        elif self.path == "/sync":
            log.info("HTTP: POST /sync -> manual sync requested")
            threading.Thread(target=sync_to_remote, daemon=True).start()
            self.send_response(200)
        elif self.path == "/stats":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with stats_lock:
                self.wfile.write(json.dumps(stats, indent=2).encode())
            return
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
    server = HTTPServer(("127.0.0.1", 8900), Handler)
    log.info(f"ADB helper listening on 127.0.0.1:8900")
    log.info(f"Captured photos saved to: {os.path.abspath(LOCAL_SAVE_DIR)}")
    log.info(f"Remote sync: every {BATCH_SIZE} files -> {REMOTE_HOST}:{REMOTE_PATH}")
    log.info(f"Logs: {os.path.abspath(LOG_DIR)}/slideshow.log")
    log.info(f"Errors: {os.path.abspath(LOG_DIR)}/slideshow_errors.log")
    server.serve_forever()
