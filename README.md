# Slideshow

A dead-simple local image slideshow powered by nginx. No frameworks, no build step — one HTML file, a shell script, and a Python helper for ADB integration.

## Setup

**Prerequisites:** nginx, python3, adb (optional — for camera trigger)

```bash
# macOS
brew install nginx

# Linux
sudo apt install nginx
```

## Usage

```bash
# Default: serves images from ./images/
./slideshow.sh start

# Custom image directory
SLIDESHOW_IMAGES=/path/to/your/photos ./slideshow.sh start

# Open http://localhost:8899

# Stop
./slideshow.sh stop
```

## Controls

| Key | Action |
|---|---|
| `→` / `Space` | Next image |
| `←` | Previous image |
| `P` | Pause |
| `R` | Resume |
| `F` / double-click | Fullscreen |

## ADB Camera Trigger

Every time a new image is displayed, the server sends `adb shell input keyevent 27` (camera shutter) to a connected Android device. The captured photo is then renamed to match the slideshow image and moved to `/sdcard/DCIM/Slideshow/` on the phone.

Disable by simply not connecting a phone — the trigger fails silently.

## How it works

- `slideshow.sh` generates an nginx config at runtime (in `/tmp`) — no hardcoded paths.
- nginx serves `index.html` at the root and the image directory with JSON autoindex.
- The web page fetches the image list, sorts it, and runs the slideshow.
- `adb_helper.py` runs alongside as a tiny HTTP server that nginx proxies to at `/api/`.
