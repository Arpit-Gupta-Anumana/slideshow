#!/usr/bin/env python3
"""Tiny HTTP server: triggers camera shutter via ADB and renames the captured photo."""

import json
import os
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"
CAPTURE_WAIT = 4  # seconds to wait for the photo to be saved

def get_latest_photo():
    """Return the filename of the most recent file in the camera folder."""
    result = subprocess.run(
        ["adb", "shell", f"ls -t {CAMERA_DIR}/ | head -1"],
        capture_output=True, text=True
    )
    name = result.stdout.strip()
    return name if name else None

def capture_and_rename(target_name):
    """Fire shutter, wait for photo, rename it to match the slideshow image."""
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

    subprocess.run(
        ["adb", "shell", f"mkdir -p {RENAME_DIR}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["adb", "shell", f"mv '{CAMERA_DIR}/{after}' '{RENAME_DIR}/{new_name}'"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"[ok] {after} -> {RENAME_DIR}/{new_name}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/trigger":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                target_name = body.get("name", "unknown.jpg")
                threading.Thread(target=capture_and_rename, args=(target_name,), daemon=True).start()
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
    server.serve_forever()
