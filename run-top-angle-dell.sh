#!/bin/bash
# Slideshow for top_angle_dell; ADB renames captures as <name>_top_angle_dell.<ext>
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SLIDESHOW_IMAGES="${SLIDESHOW_IMAGES:-/home/nference/top_angle_dell}"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
