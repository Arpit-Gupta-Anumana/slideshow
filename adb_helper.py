#!/usr/bin/env python3
"""Tiny HTTP server: triggers camera shutter via ADB, renames, pulls to Mac,
uploads to remote server in batches, cleans up phone and local storage."""

import json
import os
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"
CAPTURE_WAIT = 4

LOCAL_SAVE_DIR = os.environ.get("CAPTURE_DIR", "./captured")

REMOTE_HOST = os.environ.get("REMOTE_HOST", "arpit.gupta@dev3.nferx.com")
REMOTE_PATH = os.environ.get("REMOTE_PATH", "/dev3-datastore/arpit/LEF/")
SSH_KEY = os.environ.get("SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1000"))

pull_count = 0
pull_count_lock = threading.Lock()
upload_in_progress = threading.Lock()

def get_latest_photo():
    result = subprocess.run(
        ["adb", "shell", f"ls -t {CAMERA_DIR}/ | head -1"],
        capture_output=True, text=True
    )
    name = result.stdout.strip()
    return name if name else None

def sync_to_remote():
    """rsync captured files to remote server, delete local copies on success."""
    if not upload_in_progress.acquire(blocking=False):
        print("[sync] upload already in progress, skipping")
        return

    try:
        local_dir = LOCAL_SAVE_DIR.rstrip("/") + "/"
        result = subprocess.run(
            [
                "rsync", "-avz", "--remove-source-files",
                "-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no",
                local_dir,
                f"{REMOTE_HOST}:{REMOTE_PATH}"
            ],
            capture_output=True, text=True
        )

        if result.returncode == 0:
            synced = [l for l in result.stdout.splitlines() if not l.startswith("sending") and not l.startswith("sent") and not l.startswith("total") and l.strip()]
            print(f"[sync] uploaded {len(synced)} files to {REMOTE_HOST}:{REMOTE_PATH}")
        else:
            print(f"[sync-error] rsync failed: {result.stderr.strip()}")
    finally:
        upload_in_progress.release()

def maybe_trigger_sync():
    global pull_count
    with pull_count_lock:
        pull_count += 1
        if pull_count >= BATCH_SIZE:
            pull_count = 0
            print(f"[sync] batch of {BATCH_SIZE} reached, starting upload...")
            threading.Thread(target=sync_to_remote, daemon=True).start()

def capture_rename_pull(target_name):
    before = get_latest_photo()

    subprocess.run(
        ["adb", "shell", "input", "keyevent", "27"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    time.sleep(CAPTURE_WAIT)

    after = get_latest_photo()
    if not after or after == before:
        print(f"[warn] No new photo detected for: {target_name}")
        return

    ext = os.path.splitext(after)[1]
    base = os.path.splitext(target_name)[0]
    new_name = base + ext
    phone_path = f"{RENAME_DIR}/{new_name}"

    subprocess.run(
        ["adb", "shell", f"mkdir -p {RENAME_DIR}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["adb", "shell", f"mv '{CAMERA_DIR}/{after}' '{phone_path}'"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"[rename] {after} -> {phone_path}")

    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
    local_path = os.path.join(LOCAL_SAVE_DIR, new_name)

    pull = subprocess.run(
        ["adb", "pull", phone_path, local_path],
        capture_output=True, text=True
    )

    if pull.returncode == 0:
        subprocess.run(
            ["adb", "shell", f"rm '{phone_path}'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[ok] pulled -> {local_path} (phone cleaned)")
        maybe_trigger_sync()
    else:
        print(f"[warn] pull failed for {phone_path}, keeping on phone")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/trigger":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                target_name = body.get("name", "unknown.jpg")
                threading.Thread(target=capture_rename_pull, args=(target_name,), daemon=True).start()
                self.send_response(200)
            except Exception as e:
                print(f"[error] {e}")
                self.send_response(500)
        elif self.path == "/sync":
            threading.Thread(target=sync_to_remote, daemon=True).start()
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
    server = HTTPServer(("127.0.0.1", 8900), Handler)
    print(f"ADB helper listening on 127.0.0.1:8900")
    print(f"Captured photos saved to: {os.path.abspath(LOCAL_SAVE_DIR)}")
    print(f"Remote sync: every {BATCH_SIZE} files -> {REMOTE_HOST}:{REMOTE_PATH}")
    server.serve_forever()
