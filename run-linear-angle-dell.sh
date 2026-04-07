#!/bin/bash
# Slideshow for Linear_Angle_Dell images; ADB renames captures as <name>_Linear_Angle_Dell.<ext>
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SLIDESHOW_IMAGES="${SLIDESHOW_IMAGES:-/home/nference/Linear_Angle_Dell}"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
