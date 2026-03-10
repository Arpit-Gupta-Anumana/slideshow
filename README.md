# Slideshow

A dead-simple local image slideshow powered by nginx. No frameworks, no build step — one HTML file and a shell script.

## Setup

**Prerequisites:** nginx (`brew install nginx` on macOS, `apt install nginx` on Linux)

```bash
git clone <this-repo> slideshow
cd slideshow

# Add your images
cp /path/to/photos/*.jpg images/

# Start
./slideshow.sh start

# Open http://localhost:8899

# Stop
./slideshow.sh stop
```

## Controls

| Key / Action | Effect |
|---|---|
| `Space` / `→` | Next image |
| `←` | Previous image |
| `P` / `K` | Pause / Resume |
| Double-click | Fullscreen |
| Speed slider | 0.5s – 15s per image |
| Drag & drop | Add images on-the-fly |

## How it works

- `slideshow.sh` generates an nginx config at runtime (in `/tmp`) pointing at whatever directory you cloned into — no hardcoded paths.
- nginx serves `index.html` at the root and the `images/` folder with JSON autoindex.
- The web page fetches the image list from `/images/`, sorts it, and runs the slideshow. That's it.
