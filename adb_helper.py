#!/usr/bin/env python3
"""Tiny HTTP server: triggers camera shutter via ADB, renames, pulls to Mac, cleans up phone."""

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

def get_latest_photo():
    result = subprocess.run(
        ["adb", "shell", f"ls -t {CAMERA_DIR}/ | head -1"],
        capture_output=True, text=True
    )
    name = result.stdout.strip()
    return name if name else None

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
    server.serve_forever()
