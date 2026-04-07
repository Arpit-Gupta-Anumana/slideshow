#!/bin/bash
# Slideshow for 2x_Zoom_Dell; ADB renames captures as <name>_2x_Zoom_Dell.<ext>
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SLIDESHOW_IMAGES="${SLIDESHOW_IMAGES:-/home/nference/2x_Zoom_Dell}"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
