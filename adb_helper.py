#!/usr/bin/env python3
"""Tiny HTTP server: triggers camera shutter via ADB and renames the captured photo."""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

CAMERA_DIR = "/sdcard/DCIM/Camera"
RENAME_DIR = "/sdcard/DCIM/Slideshow"
# Max time to wait for the new file after shutter (poll often; exit as soon as it appears)
CAPTURE_WAIT = float(os.environ.get("SLIDESHOW_CAPTURE_WAIT", "3.5"))
CAPTURE_POLL = float(os.environ.get("SLIDESHOW_CAPTURE_POLL", "0.15"))
# When 1 (default): POST /trigger blocks until rename done; browser must await (strict pairing).
SYNC_CAPTURE = os.environ.get("SLIDESHOW_SYNC_CAPTURE", "1").strip().lower() not in ("0", "false", "no", "")
AUDIT_LOG_PATH = os.environ.get("SLIDESHOW_CAPTURE_LOG", "/tmp/slideshow-capture-audit.log").strip()

IMG_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng", ".bmp"}
)

WORK_QUEUE = queue.Queue()
_CAPTURE_LOCK = threading.Lock()


def _shell_safe_segment(s):
    """Avoid breaking adb shell single-quoted paths."""
    return (s or "").replace("'", "_")


def build_target_filename(slideshow_basename, captured_ext):
    """
    Phone filename: {base}[_{folder}][_{extra}]{ext}
    folder = SLIDESHOW_FOLDER_LABEL (basename of image dir by default)
    extra = optional SLIDESHOW_CAPTURE_SUFFIX (e.g. beach)
    """
    base = _shell_safe_segment(slideshow_basename)
    folder = _shell_safe_segment(os.environ.get("SLIDESHOW_FOLDER_LABEL", "").strip())
    extra = _shell_safe_segment(os.environ.get("SLIDESHOW_CAPTURE_SUFFIX", "").strip())
    ext = captured_ext if captured_ext else ".jpg"
    parts = [base]
    if folder:
        parts.append(folder)
    if extra:
        parts.append(extra)
    return "_".join(parts) + ext


def _is_camera_image_filename(name):
    ext = os.path.splitext(name)[1].lower()
    return ext in IMG_EXTENSIONS


def adb_available():
    """Return True if adb is installed and in PATH."""
    return shutil.which("adb") is not None


def device_connected():
    """Return True if at least one Android device is connected and authorized."""
    if not adb_available():
        return False
    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [l for l in r.stdout.strip().splitlines() if l and not l.startswith("List")]
        return any("device" in l and "unauthorized" not in l for l in lines)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _camera_files_ordered_newest_first():
    """Filenames in DCIM/Camera, newest first (same order as ls -t)."""
    if not adb_available():
        return []
    try:
        result = subprocess.run(
            ["adb", "shell", f"ls -t {CAMERA_DIR}/ 2>/dev/null"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _find_new_camera_file_after_shutter(before_set):
    """
    After shutter, return the newest *image* file that was not in before_set.
    Ignores new non-images (e.g. sidecar/metadata). ls -t order = newest first among new images.
    """
    deadline = time.time() + CAPTURE_WAIT
    ambiguous_logged = False
    while time.time() < deadline:
        time.sleep(CAPTURE_POLL)
        ordered = _camera_files_ordered_newest_first()
        new_images = [n for n in ordered if n not in before_set and _is_camera_image_filename(n)]
        if not new_images:
            continue
        if len(new_images) > 1 and not ambiguous_logged:
            # Multiple new images this tick (e.g. RAW+JPEG); use newest (first in ls -t).
            print(f"[info] Multiple new camera files; using newest: {new_images[0]} (also: {new_images[1:]})")
            ambiguous_logged = True
        return new_images[0]
    return None


def _append_audit(payload: dict):
    if not AUDIT_LOG_PATH:
        return
    try:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        print(f"[warn] audit log: {e}")


def _verify_dest_exists(dest_basename):
    """Return True if Slideshow/dest_basename exists on device."""
    safe = _shell_safe_segment(dest_basename)
    try:
        r = subprocess.run(
            ["adb", "shell", f"test -f '{RENAME_DIR}/{safe}' && echo ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "ok" in (r.stdout or "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_one_capture(target_name):
    """
    Full pipeline for one slide. Returns dict for JSON API / audit.
    ok=True only if we see a new image, mv succeeds, and dest file exists.
    """
    base_result = {
        "ok": False,
        "target": target_name,
        "src": None,
        "dest": None,
        "error": None,
    }
    if not adb_available():
        base_result["error"] = "adb_not_found"
        _append_audit(base_result)
        return base_result
    if not device_connected():
        base_result["error"] = "no_device"
        _append_audit(base_result)
        return base_result

    before_set = set(_camera_files_ordered_newest_first())

    try:
        sh = subprocess.run(
            ["adb", "shell", "input", "keyevent", "27"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if sh.returncode != 0:
            base_result["error"] = "shutter_failed"
            _append_audit(base_result)
            return base_result
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        base_result["error"] = f"shutter_exception:{e}"
        _append_audit(base_result)
        return base_result

    after = _find_new_camera_file_after_shutter(before_set)
    if not after:
        base_result["error"] = f"no_new_image_within_{CAPTURE_WAIT}s"
        print(f"[warn] No new camera image for: {target_name} (waited {CAPTURE_WAIT}s)")
        _append_audit(base_result)
        return base_result

    ext = os.path.splitext(after)[1]
    slideshow_base = os.path.splitext(target_name)[0]
    new_name = build_target_filename(slideshow_base, ext)
    base_result["src"] = after
    base_result["dest"] = new_name

    try:
        subprocess.run(
            ["adb", "shell", f"mkdir -p {RENAME_DIR}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        mv = subprocess.run(
            ["adb", "shell", f"mv '{CAMERA_DIR}/{_shell_safe_segment(after)}' '{RENAME_DIR}/{new_name}'"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if mv.returncode != 0:
            base_result["error"] = "mv_failed"
            _append_audit(base_result)
            return base_result
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        base_result["error"] = f"mv_exception:{e}"
        _append_audit(base_result)
        return base_result

    if not _verify_dest_exists(new_name):
        base_result["error"] = "dest_missing_after_mv"
        _append_audit(base_result)
        return base_result

    base_result["ok"] = True
    print(f"[ok] {target_name} -> {RENAME_DIR}/{new_name}")
    _append_audit(base_result)
    return base_result


def _capture_worker():
    while True:
        target_name = WORK_QUEUE.get()
        try:
            run_one_capture(target_name)
        except Exception as e:
            print(f"[error] capture worker: {e}")
        finally:
            WORK_QUEUE.task_done()


threading.Thread(target=_capture_worker, daemon=True, name="adb-capture").start()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/trigger":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            target_name = body.get("name", "unknown.jpg")

            if SYNC_CAPTURE:
                with _CAPTURE_LOCK:
                    result = run_one_capture(target_name)
                self._send_json(200 if result["ok"] else 500, result)
            else:
                WORK_QUEUE.put(target_name)
                self._send_json(200, {"ok": True, "queued": True, "target": target_name})
        except Exception as e:
            print(f"[error] {e}")
            self._send_json(500, {"ok": False, "error": str(e), "target": None})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8900), Handler)
    print("ADB helper listening on 127.0.0.1:8900")
    mode = "sync (POST waits until rename verified)" if SYNC_CAPTURE else "async (queued)"
    print(f"Capture mode: {mode}; wait up to {CAPTURE_WAIT}s per shot; audit: {AUDIT_LOG_PATH or '(off)'}")
    if not adb_available():
        print("[warn] adb not found. Install with: sudo apt install adb")
        print("       Camera trigger will do nothing until adb is installed.")
    elif not device_connected():
        print("[warn] No Android device connected. Connect a phone with USB debugging enabled.")
        print("       Camera trigger will do nothing until a device is connected.")
    else:
        folder = os.environ.get("SLIDESHOW_FOLDER_LABEL", "").strip()
        extra = os.environ.get("SLIDESHOW_CAPTURE_SUFFIX", "").strip()
        print(f"Renamed photos will go to: {RENAME_DIR}/")
        if folder and extra:
            print(f"Rename pattern: <stem>_{folder}_{extra}<ext>")
        elif folder:
            print(f"Rename pattern: <stem>_{folder}<ext>")
        elif extra:
            print(f"Rename pattern: <stem>_{extra}<ext>")
        else:
            print("Rename pattern: <stem><ext>")
    server.serve_forever()
