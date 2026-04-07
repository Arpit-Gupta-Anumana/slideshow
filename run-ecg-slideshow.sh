#!/bin/bash
# Run the slideshow using ECG images from ecg_image_models image-gen output.
# Usage: ./run-ecg-slideshow.sh [start|stop|restart]

ECG_IMAGES_DIR="${ECG_IMAGES_DIR:-/home/nference/ecg_image_models/modules/image-gen/ecgs}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$ECG_IMAGES_DIR" ]; then
  echo "Error: Image directory not found: $ECG_IMAGES_DIR" >&2
  echo "Set ECG_IMAGES_DIR to override." >&2
  exit 1
fi

export SLIDESHOW_IMAGES="$ECG_IMAGES_DIR"
exec "$SCRIPT_DIR/slideshow.sh" "${1:-start}"
