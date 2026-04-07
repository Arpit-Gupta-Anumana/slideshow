# Slideshow

A dead-simple local image slideshow powered by nginx. No frameworks, no build step — one HTML file, a shell script, and a Python helper for ADB integration.

## Setup

**Prerequisites:** nginx, python3, adb (optional — for camera trigger)

```bash
# macOS
brew install nginx

# Linux
sudo apt install nginx

# ADB (optional — for Android camera trigger when a device is connected)
sudo apt install adb   # Linux
brew install android-platform-tools   # macOS
```

You can also drop Android `platform-tools` into `.tools/platform-tools/` in the repo; `slideshow.sh` prepends that to `PATH` when `adb` exists there.

On Linux, add your user to the `plugdev` group so adb can see the device without root:
`sudo usermod -aG plugdev $USER` (then log out and back in). Enable **USB debugging** on the phone and accept the authorization prompt when connected.

## Usage

```bash
# Default: serves images from ./images/
./slideshow.sh start

# Custom image directory
SLIDESHOW_IMAGES=/path/to/your/photos ./slideshow.sh start

# ECG images (from ecg_image_models image-gen)
SLIDESHOW_IMAGES=/home/nference/ecg_image_models/modules/image-gen/ecgs ./slideshow.sh start
# Or use the helper (same path by default):
./run-ecg-slideshow.sh start

# Linear_Angle_Dell folder (helper sets path; ADB renames to `basename_Linear_Angle_Dell.ext`)
./run-linear-angle-dell.sh start

# 2x_Zoom_Dell folder
./run-2x-zoom-dell.sh start

# top_angle_dell folder
./run-top-angle-dell.sh start

# 16k_images with phone suffix `2x_zoom_samsung_landscape` (not the directory name)
./run-16k-samsung-landscape.sh start

# Open http://localhost:8899

# Stop
./slideshow.sh stop
# Or: ./run-ecg-slideshow.sh stop
```

## Controls

| Key | Action |
|---|---|
| `→` / `Space` | Next image (stops after the last image — no loop) |
| `←` | Previous image (after finishing, go back to browse again) |
| `P` | Pause |
| `R` | Resume |
| `F` / double-click | Fullscreen |

## ADB Camera Trigger

Every time a new image is displayed, the server sends `adb shell input keyevent 27` (camera shutter) to a connected Android device. The captured photo is then moved to `/sdcard/DCIM/Slideshow/` on the phone and renamed to:

`{slideshow_image_basename}_{folder_label}{captured_extension}`

For example, showing `10004.jpg` from a folder named `Linear_Angle_Dell` produces `10004_Linear_Angle_Dell.jpg` (the extension comes from the phone’s capture).

`folder_label` defaults to the **basename** of `SLIDESHOW_IMAGES` (e.g. `Linear_Angle_Dell`). Override with:

`SLIDESHOW_FOLDER_LABEL=MyLabel ./slideshow.sh start`

Optional extra segment before the extension: `SLIDESHOW_CAPTURE_SUFFIX=beach` → `10004_MyLabel_beach.jpg`. Default is off (empty).

### Reliability (avoid wrong / lost names on the phone)

- **Synchronous capture (default):** `SLIDESHOW_SYNC_CAPTURE=1` makes each `POST /api/trigger` **block until** the new camera file is found, **moved**, and **verified** on the device. The browser **waits** before starting the next slide timer, so auto-advance cannot outrun the phone.
- **Audit log:** Every attempt (success or failure) appends one **JSON line** to `SLIDESHOW_CAPTURE_LOG` (default `/tmp/slideshow-capture-audit.log`) with `target`, `src`, `dest`, `ok`, `error`. If something goes wrong, grep that file instead of guessing which slide failed.
- **Image-only detection:** Only new files with common **photo** extensions (e.g. `.jpg`, `.png`, `.heic`) are treated as captures—random new files in `DCIM/Camera` are ignored.
- **Legacy fast mode (riskier):** `SLIDESHOW_SYNC_CAPTURE=0` restores fire-and-forget queuing only if you accept possible mis-ordering under load.

Captures run **one at a time** in a queue so the wrong photo is not paired with the wrong filename. Defaults target **~2–3 seconds per image** plus **sync wait** (3s pause after each successful capture before auto-advance, up to 3.5s wait for the new file, polling every 0.15s).

If you see missed or mismatched renames on a slow phone, raise waits:

```bash
SLIDESHOW_CAPTURE_WAIT=6 SLIDESHOW_CAPTURE_POLL=0.25 SLIDESHOW_IMAGES=/path ./slideshow.sh start
```

And increase `interval` in `index.html` to stay above typical save time.

(After changing `adb_helper.py`, run `./slideshow.sh stop` then `./slideshow.sh start` so the helper reloads.)

Disable by simply not connecting a phone — the trigger fails silently.

## How it works

- `slideshow.sh` generates an nginx config at runtime (in `/tmp`) — no hardcoded paths.
- nginx serves `index.html` at the root and the image directory with JSON autoindex.
- The web page fetches the image list, sorts it, and runs the slideshow.
- `adb_helper.py` runs alongside as a tiny HTTP server that nginx proxies to at `/api/`.
