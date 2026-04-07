# Slideshow + ADB Capture Pipeline

A local image slideshow web app that can automatically trigger an Android phone's camera on each slide, rename the captured photo to match the displayed image, pull it to your computer, and batch-upload it to a remote server. Zero frameworks, zero build steps — nginx, one HTML file, one Python script, one shell script.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [How It Works (Step by Step)](#how-it-works-step-by-step)
- [Keyboard Controls](#keyboard-controls)
- [Configuration Reference](#configuration-reference)
- [API Endpoints](#api-endpoints)
- [Logging and Debugging](#logging-and-debugging)
- [Capture Modes](#capture-modes)
- [Filename Renaming](#filename-renaming)
- [Pull to Local Machine](#pull-to-local-machine)
- [Remote Sync (rsync)](#remote-sync-rsync)
- [Resuming a Slideshow](#resuming-a-slideshow)
- [Run Scripts (Presets)](#run-scripts-presets)
- [Bundled ADB](#bundled-adb)
- [Troubleshooting](#troubleshooting)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (index.html)                                           │
│  - Fetches image list from nginx (JSON autoindex)               │
│  - Displays slideshow: auto-advances, stops at last slide       │
│  - On each slide: POST /api/trigger { name: "image.jpg" }      │
│  - Awaits response (sync mode) or fires and forgets (async)     │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP (port 8899)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  nginx                                                          │
│  - Serves index.html at /                                       │
│  - Serves image directory at /images/ with JSON autoindex       │
│  - Proxies /api/* to adb_helper.py on port 8900                 │
│  - Logs access + errors to LOG_DIR                              │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP proxy (port 8900)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  adb_helper.py (Python HTTP server)                             │
│                                                                 │
│  For each image trigger:                                        │
│  1. Pre-check: adb installed? device connected?                 │
│  2. Snapshot camera dir (ls -t /sdcard/DCIM/Camera/)            │
│  3. Fire shutter: adb shell input keyevent 27                   │
│  4. Poll for new photo (configurable interval + stability)      │
│  5. Rename: mv to /sdcard/DCIM/Slideshow/<new_name>             │
│  6. Verify renamed file exists on phone                         │
│  7. [Optional] Pull to local machine via adb pull               │
│  8. [Optional] Clean up phone after successful pull             │
│  9. [Optional] Batch rsync to remote server                     │
│                                                                 │
│  Endpoints: POST /trigger, POST /sync, POST /stats              │
│  Logging: verbose per-step log, error log, JSON audit log       │
└────────────────────────┬────────────────────────────────────────┘
                         │ USB / wireless ADB
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Android Phone                                                  │
│  - Camera app must be open and ready                            │
│  - Photos land in /sdcard/DCIM/Camera/                          │
│  - Renamed photos moved to /sdcard/DCIM/Slideshow/              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Tool | Install | Required? |
|---|---|---|
| **nginx** | `brew install nginx` (macOS) / `sudo apt install nginx` (Linux) | Yes |
| **python3** | Pre-installed on macOS and most Linux | Yes |
| **adb** | `brew install android-platform-tools` (macOS) / `sudo apt install adb` (Linux) | Only if using camera capture |
| **rsync** | Pre-installed on macOS and most Linux | Only if using remote sync |

---

## Project Structure

```
slideshow/
├── slideshow.sh          # Start/stop/restart script (generates nginx config at runtime)
├── adb_helper.py         # Python capture pipeline server
├── index.html            # Slideshow web UI (single file, no dependencies)
├── images/               # Default image directory (or use SLIDESHOW_IMAGES)
│   └── .gitkeep
├── .tools/               # (Optional) Bundled platform-tools/adb
│   └── platform-tools/
│       └── adb
├── run-*.sh              # (Optional) Preset scripts for different capture setups
├── README.md
└── .gitignore
```

---

## Quick Start

### Just a slideshow (no phone, no capture)

```bash
# Put images in ./images/ and start
./slideshow.sh start
# Open http://localhost:8899
```

### Slideshow + phone capture (rename only, photos stay on phone)

```bash
# Connect phone via USB, open camera app
SLIDESHOW_IMAGES=/path/to/images ./slideshow.sh start
```

### Full pipeline (capture + pull to Mac + rsync to server)

```bash
SLIDESHOW_IMAGES=/path/to/images \
SLIDESHOW_CAPTURE_SUFFIX=my_setup \
SLIDESHOW_CAPTURE=/path/to/save/locally \
REMOTE_HOST=user@server.com \
REMOTE_PATH=/data/captures/ \
BATCH_SIZE=500 \
./slideshow.sh start
```

### Stop

```bash
./slideshow.sh stop
```

---

## How It Works (Step by Step)

When the slideshow advances to a new image, here is exactly what happens:

### 1. Browser displays the image
The browser loads the image from nginx and shows it fullscreen.

### 2. Browser sends trigger
`POST /api/trigger` with `{ "name": "000121212__only_ecg.jpeg" }` is sent to the backend. In **sync mode** (default), the browser waits for the full capture pipeline to complete before auto-advancing. In **async mode**, it fires and forgets.

### 3. Pre-flight checks
`adb_helper.py` checks:
- Is `adb` installed and in PATH?
- Is an Android device connected and authorized?

If either fails, it returns an error immediately. The browser pauses and shows an error message.

### 4. Snapshot camera directory
Lists all files currently in `/sdcard/DCIM/Camera/` on the phone. This is the "before" set — used to detect which file is new after the shutter fires.

### 5. Fire camera shutter
Runs `adb shell input keyevent 27` which simulates the hardware camera button.

### 6. Poll for new photo
Instead of a blind fixed-second sleep, the helper **polls** the camera directory every 0.3 seconds (configurable). It looks for a new image file that wasn't in the "before" set. To avoid catching a half-written file, it requires the filename to appear consistently for 2 consecutive polls (**stability check**). Exits as soon as the file is confirmed — no wasted time. Gives up after 8 seconds (configurable).

### 7. Rename on phone
Moves the captured photo from `/sdcard/DCIM/Camera/IMG_xxx.jpg` to `/sdcard/DCIM/Slideshow/<new_name>.jpg`. The new name is built from the slideshow image name plus optional folder label and suffix (see [Filename Renaming](#filename-renaming)). Uses proper shell quoting to handle filenames with special characters.

### 8. Verify rename
Runs `test -f` on the phone to confirm the file actually exists at the new path. If it doesn't (rare edge case), the capture is marked as failed.

### 9. Pull to local machine (optional)
If `SLIDESHOW_CAPTURE` / `CAPTURE_DIR` is set, runs `adb pull` to copy the photo from phone to your computer. On success, deletes the photo from the phone. On failure, keeps it on the phone as a safety net.

### 10. Batch rsync to remote (optional)
If `REMOTE_HOST` and `REMOTE_PATH` are set, a counter tracks successful pulls. Every `BATCH_SIZE` pulls (default 1000), it triggers `rsync --remove-source-files` to upload the local captures to the remote server and delete local copies. You can also force a sync anytime via the API.

### 11. Logging and audit
Every step is logged with timestamps, command output, exit codes, and timing. A JSON audit log records one line per capture for machine-readable post-processing.

---

## Keyboard Controls

| Key | Action |
|---|---|
| `→` or `Space` | Next image (triggers capture) |
| `←` | Previous image (triggers capture) |
| `P` | Pause slideshow |
| `R` | Resume slideshow |
| `F` or double-click | Toggle fullscreen |

The slideshow **stops automatically** at the last image and shows "Slideshow finished". It does not loop.

---

## Configuration Reference

All configuration is via environment variables. Every feature is opt-in — if you don't set a variable, it's disabled or uses a safe default.

### Image Source

| Variable | Default | Description |
|---|---|---|
| `SLIDESHOW_IMAGES` | `./images` | Directory containing the source images to display |

### Capture Naming

| Variable | Default | Description |
|---|---|---|
| `SLIDESHOW_FOLDER_LABEL` | *(empty)* | Middle segment in filename: `stem_{label}_{suffix}.ext` |
| `SLIDESHOW_CAPTURE_SUFFIX` | *(empty)* | End segment in filename: `stem_{label}_{suffix}.ext` |

### Capture Behavior

| Variable | Default | Description |
|---|---|---|
| `SLIDESHOW_SYNC_CAPTURE` | `1` | `1` = browser waits for capture to finish (recommended). `0` = fire-and-forget (browser advances immediately) |
| `SLIDESHOW_CAPTURE_MAX_WAIT` | `8` | Max seconds to poll for a new photo after shutter |
| `SLIDESHOW_CAPTURE_POLL` | `0.3` | Seconds between camera directory polls |
| `SLIDESHOW_STABILITY_TICKS` | `2` | Filename must appear this many consecutive polls to be considered stable (handles slow camera writes) |
| `SLIDESHOW_ADB_TIMEOUT` | `30` | Timeout in seconds for individual ADB commands |

### Local Pull

| Variable | Default | Description |
|---|---|---|
| `SLIDESHOW_CAPTURE` | *(empty)* | Local directory to pull captured photos into. If empty, pull is disabled — photos stay on the phone |

### Remote Sync

| Variable | Default | Description |
|---|---|---|
| `REMOTE_HOST` | *(empty)* | SSH target for rsync (e.g. `user@server.com`). If empty, sync is disabled |
| `REMOTE_PATH` | *(empty)* | Path on remote server (e.g. `/data/captures/`) |
| `SSH_KEY` | `~/.ssh/id_ed25519` | SSH private key for rsync |
| `BATCH_SIZE` | `1000` | Trigger rsync after this many successful pulls |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_DIR` | `/tmp` | Directory for all log files |
| `SLIDESHOW_CAPTURE_LOG` | `$LOG_DIR/capture_audit.jsonl` | Path to the JSON audit log |

---

## API Endpoints

All available at `http://localhost:8899/api/`.

### `POST /api/trigger`

Trigger a capture for a specific image. Normally called by the browser automatically.

```bash
curl -X POST http://localhost:8899/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"name": "test_image.jpg"}'
```

Response (sync mode):
```json
{
  "ok": true,
  "target": "test_image.jpg",
  "src": "IMG_20260407_120000.jpg",
  "dest": "test_image_my_setup.jpg",
  "error": null,
  "n": 42
}
```

### `POST /api/sync`

Force an immediate rsync to the remote server, regardless of batch count.

```bash
curl -X POST http://localhost:8899/api/sync
```

### `POST /api/stats`

Get current pipeline statistics.

```bash
curl http://localhost:8899/api/stats
```

Response:
```json
{
  "triggered": 150,
  "captured": 148,
  "capture_failed": 2,
  "renamed": 148,
  "rename_failed": 0,
  "pulled": 148,
  "pull_failed": 0,
  "phone_cleaned": 148,
  "synced": 100,
  "sync_failed": 0
}
```

---

## Logging and Debugging

All logs are written to `LOG_DIR` (default `/tmp`).

| File | What it contains |
|---|---|
| `slideshow.log` | **Everything** — every step of every photo with timestamps, commands, exit codes, stdout/stderr, timing. This is your primary debugging tool. |
| `slideshow_errors.log` | **Errors and warnings only** — quick way to see what went wrong without scrolling through thousands of success lines. |
| `capture_audit.jsonl` | **One JSON line per capture** — machine-readable structured log. Each line has: timestamp, target name, source photo, destination name, ok/error status. Use for post-processing, counting, or feeding into other tools. |
| `slideshow-nginx-access.log` | nginx HTTP access log |
| `slideshow-nginx-error.log` | nginx error log |

### Example: slideshow.log for one photo

```
2026-04-07 16:45:40.123 [INFO] ============================================================
2026-04-07 16:45:40.123 [INFO] PHOTO #42: '000121212__only_ecg.jpeg'
2026-04-07 16:45:40.123 [INFO] ============================================================
2026-04-07 16:45:40.124 [INFO]   STEP 1/7: listing camera dir before shutter
2026-04-07 16:45:40.124 [INFO]   CMD [get_camera_files]: adb shell ls -t /sdcard/DCIM/Camera/ 2>/dev/null
2026-04-07 16:45:40.450 [INFO]   CMD [get_camera_files]: exit=0 (0.33s)
2026-04-07 16:45:40.450 [INFO]   1247 existing files in camera dir
2026-04-07 16:45:40.450 [INFO]   STEP 2/7: firing camera shutter (keyevent 27)
2026-04-07 16:45:40.451 [INFO]   CMD [shutter]: adb shell input keyevent 27
2026-04-07 16:45:40.620 [INFO]   CMD [shutter]: exit=0 (0.17s)
2026-04-07 16:45:40.620 [INFO]   STEP 3/7: polling for new photo (max 8s, every 0.3s, 2 stability ticks)
2026-04-07 16:45:42.850 [INFO]   CAPTURE OK: IMG_20260407_164542.jpg
2026-04-07 16:45:42.850 [INFO]   STEP 4/7: renaming IMG_20260407_164542.jpg -> /sdcard/DCIM/Slideshow/000121212__only_ecg.jpg
2026-04-07 16:45:43.100 [INFO]   RENAME OK: /sdcard/DCIM/Slideshow/000121212__only_ecg.jpg
2026-04-07 16:45:43.100 [INFO]   STEP 5/7: verifying renamed file exists on phone
2026-04-07 16:45:43.350 [INFO]   STEP 6/7: pulling to Mac -> /path/to/captured/000121212__only_ecg.jpg
2026-04-07 16:45:44.200 [INFO]   PULL OK: /path/to/captured/000121212__only_ecg.jpg
2026-04-07 16:45:44.200 [INFO]   PHONE CLEANED: /sdcard/DCIM/Slideshow/000121212__only_ecg.jpg
2026-04-07 16:45:44.200 [INFO]   STEP 7/7: checking batch sync
2026-04-07 16:45:44.200 [INFO]   BATCH: 42/1000
2026-04-07 16:45:44.200 [INFO]   PHOTO #42 COMPLETE: 000121212__only_ecg.jpeg -> 000121212__only_ecg.jpg
2026-04-07 16:45:44.200 [INFO]   STATS: {"triggered":42,"captured":42,...}
```

### Example: capture_audit.jsonl

```json
{"ts":"2026-04-07T11:15:44.200Z","ok":true,"target":"000121212__only_ecg.jpeg","src":"IMG_20260407_164542.jpg","dest":"000121212__only_ecg.jpg","error":null,"n":42}
{"ts":"2026-04-07T11:15:52.100Z","ok":false,"target":"000006666__only_ecg.jpeg","src":null,"dest":null,"error":"no_new_image_within_8s","n":43}
```

---

## Capture Modes

### Sync mode (`SLIDESHOW_SYNC_CAPTURE=1`, default)

The browser **waits** for each capture to complete before advancing. This means:
- The slideshow only moves forward after the photo is confirmed captured and renamed
- If capture fails, the slideshow **pauses** and shows an error message
- Guarantees every displayed image gets a corresponding capture
- Slower (each slide takes as long as the capture pipeline), but reliable

### Async mode (`SLIDESHOW_SYNC_CAPTURE=0`)

The browser fires the trigger and advances immediately. Captures are queued and processed one at a time by a background worker thread. This means:
- The slideshow runs at its set speed regardless of capture time
- If the camera is slower than the slideshow, captures queue up
- No error feedback in the browser
- Faster display, but captures may lag behind

---

## Filename Renaming

The captured photo on the phone is renamed based on the slideshow image name plus optional segments:

```
{stem}[_{folder_label}][_{capture_suffix}]{ext}
```

| Slideshow image | FOLDER_LABEL | CAPTURE_SUFFIX | Result on phone |
|---|---|---|---|
| `000121212.jpeg` | *(empty)* | *(empty)* | `000121212.jpg` |
| `000121212.jpeg` | *(empty)* | `beach` | `000121212_beach.jpg` |
| `000121212.jpeg` | `mayo_images` | `samsung` | `000121212_mayo_images_samsung.jpg` |
| `ecg_lead_II.png` | `16k` | `moto_linear_dell` | `ecg_lead_II_16k_moto_linear_dell.jpg` |

The extension comes from the phone's camera (typically `.jpg`), not from the slideshow image.

---

## Pull to Local Machine

Set `SLIDESHOW_CAPTURE=/path/to/save` to enable pulling captured photos from the phone to your computer.

When enabled, after each successful rename:
1. `adb pull` copies the photo from phone to the local directory
2. If pull succeeds, `adb shell rm` deletes it from the phone
3. If pull fails, the photo is kept on the phone (safe fallback)

---

## Remote Sync (rsync)

Set `REMOTE_HOST` and `REMOTE_PATH` to enable automatic batch uploads.

- After every `BATCH_SIZE` successful pulls (default 1000), rsync runs automatically
- Uses `rsync -avz --remove-source-files` — only deletes local files after confirmed transfer
- SSH key authentication via `SSH_KEY` (default `~/.ssh/id_ed25519`)
- Force a sync anytime: `curl -X POST http://localhost:8899/api/sync`
- If rsync fails, local files are preserved

---

## Resuming a Slideshow

If the slideshow was interrupted (power outage, disconnect, etc.), you can resume from where you left off:

```
http://localhost:8899/?start=500
```

This finds the first image whose numeric prefix is >= 500 and starts from there. Works with filenames like `000500_something.jpg` — it extracts leading digits.

Check the audit log to find the last successfully captured image number.

---

## Run Scripts (Presets)

For recurring setups, create small wrapper scripts instead of typing env vars every time:

```bash
#!/bin/bash
# run-mayo-samsung.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SLIDESHOW_IMAGES="/data/mayo_images"
export SLIDESHOW_CAPTURE_SUFFIX="samsung_landscape"
export SLIDESHOW_CAPTURE="/data/captured/mayo"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
```

Then just: `./run-mayo-samsung.sh start`

---

## Bundled ADB

If you place `adb` inside the project at `.tools/platform-tools/adb`, the script will automatically add it to PATH. This is useful on machines where adb isn't globally installed:

```bash
mkdir -p .tools/platform-tools
cp /path/to/adb .tools/platform-tools/
```

---

## Troubleshooting

| Problem | Check |
|---|---|
| **"Could not reach the image server"** | Is nginx running? Run `./slideshow.sh restart`. Check `slideshow-nginx-error.log`. |
| **"Port 8899 in use"** | Kill stale processes: `lsof -ti :8899 \| xargs kill -9` and retry. |
| **"adb_not_found"** | Install adb: `brew install android-platform-tools` or place in `.tools/`. |
| **"no_device"** | Check `adb devices`. Reconnect USB. Accept "Allow USB debugging" on phone. |
| **"no_new_image_within_8s"** | Camera app not open/ready. Increase `SLIDESHOW_CAPTURE_MAX_WAIT`. Check phone storage isn't full. |
| **"shutter_failed"** | ADB connection dropped. Replug USB. Check `adb devices`. |
| **"mv_failed"** | Phone storage issue or filename with special characters. Check `slideshow_errors.log`. |
| **"Pull failed"** | USB connection unstable. Photo stays on phone safely. Will retry on next capture. |
| **rsync fails** | Check SSH key, remote host reachability: `ssh -i ~/.ssh/id_ed25519 user@host`. Check `slideshow_errors.log`. |
| **Slideshow paused unexpectedly** | In sync mode, a capture failure pauses the slideshow. Check the on-screen error. Fix the issue and press `R` to resume. |
