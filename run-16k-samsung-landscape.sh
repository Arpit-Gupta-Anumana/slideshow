#!/bin/bash
# 16k_images with explicit phone rename suffix 2x_zoom_samsung_landscape
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SLIDESHOW_IMAGES="${SLIDESHOW_IMAGES:-/home/nference/16k_images}"
export SLIDESHOW_FOLDER_LABEL="${SLIDESHOW_FOLDER_LABEL:-2x_zoom_samsung_landscape}"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
