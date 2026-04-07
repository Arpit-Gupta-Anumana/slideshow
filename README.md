# Slideshow

A dead-simple local image slideshow powered by nginx, with an ADB camera capture pipeline: display an image, trigger the phone's camera, rename the photo, pull it to your machine, and optionally rsync it to a remote server — all automated.

## Setup

**Prerequisites:** nginx, python3, adb (optional)

```bash
# macOS
brew install nginx

# Linux
sudo apt install nginx
```

## Quick Start

```bash
# Simplest: serve images from ./images/
./slideshow.sh start

# Custom image directory
SLIDESHOW_IMAGES=/path/to/photos ./slideshow.sh start

# Full pipeline: capture, rename with suffix, pull to Mac, rsync to remote
SLIDESHOW_IMAGES=/path/to/photos \
SLIDESHOW_CAPTURE_SUFFIX=my_setup \
SLIDESHOW_CAPTURE=/path/to/captured \
REMOTE_HOST=user@server.com \
REMOTE_PATH=/data/captures/ \
BATCH_SIZE=1000 \
./slideshow.sh start

# Open http://localhost:8899
# Resume from a specific image: http://localhost:8899/?start=500
```

## Controls

| Key | Action |
|---|---|
| `→` / `Space` | Next image |
| `←` | Previous image |
| `P` | Pause |
| `R` | Resume |
| `F` / double-click | Fullscreen |

## Configuration (all via environment variables)

| Variable | Default | Description |
|---|---|---|
| `SLIDESHOW_IMAGES` | `./images` | Source image directory |
| `SLIDESHOW_FOLDER_LABEL` | *(empty)* | Added to captured filename: `stem_label.jpg` |
| `SLIDESHOW_CAPTURE_SUFFIX` | *(empty)* | Added to captured filename: `stem_label_suffix.jpg` |
| `SLIDESHOW_SYNC_CAPTURE` | `1` | `1` = browser waits for capture confirmation; `0` = fire-and-forget |
| `SLIDESHOW_CAPTURE` | *(empty)* | Local dir to pull captured photos into (empty = no pull) |
| `REMOTE_HOST` | *(empty)* | rsync target host (empty = no sync) |
| `REMOTE_PATH` | *(empty)* | rsync target path |
| `SSH_KEY` | `~/.ssh/id_ed25519` | SSH key for rsync |
| `BATCH_SIZE` | `1000` | Rsync after this many pulls |
| `SLIDESHOW_CAPTURE_MAX_WAIT` | `8` | Max seconds to wait for camera |
| `SLIDESHOW_CAPTURE_POLL` | `0.3` | Seconds between polls for new photo |
| `SLIDESHOW_STABILITY_TICKS` | `2` | Consecutive polls a filename must appear to be considered stable |

## API Endpoints

```bash
# Check pipeline stats
curl http://localhost:8899/api/stats

# Force rsync now (don't wait for batch)
curl -X POST http://localhost:8899/api/sync
```

## Logging

All logs go to `LOG_DIR` (default `/tmp`):
- `slideshow.log` — full verbose log of every step of every photo
- `slideshow_errors.log` — warnings and errors only
- `capture_audit.jsonl` — one JSON line per capture (structured, machine-readable)
- `slideshow-nginx-access.log` / `slideshow-nginx-error.log` — nginx logs

## How It Works

1. `slideshow.sh` generates an nginx config at runtime — no hardcoded paths
2. nginx serves `index.html` and the image directory with JSON autoindex
3. The web page fetches the image list, sorts it, and runs the slideshow
4. Each image change triggers `adb_helper.py` which: fires the camera shutter, polls until the photo appears, renames it, optionally pulls to Mac and rsyncs to a remote server
5. In sync mode, the browser waits for each capture to complete before advancing
