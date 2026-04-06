#!/bin/bash
# Slideshow server manager

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="/tmp/slideshow-nginx.conf"
PID_FILE="/tmp/slideshow-nginx.pid"
ADB_PID_FILE="/tmp/slideshow-adb-helper.pid"
IMAGE_DIR="${SLIDESHOW_IMAGES:-$SCRIPT_DIR/images}"
PORT=8899
ADB_PORT=8900

# Phone saves as <slide_stem>_beach.<ext> (override or clear: SLIDESHOW_CAPTURE_SUFFIX=)
export SLIDESHOW_CAPTURE_SUFFIX="${SLIDESHOW_CAPTURE_SUFFIX:-beach}"

# Bundled platform-tools (adb) — not on PATH by default
if [ -x "$SCRIPT_DIR/.tools/platform-tools/adb" ]; then
  export PATH="$SCRIPT_DIR/.tools/platform-tools:$PATH"
fi

find_mime_types() {
  for f in /opt/homebrew/etc/nginx/mime.types /usr/local/etc/nginx/mime.types /etc/nginx/mime.types; do
    [ -f "$f" ] && echo "$f" && return
  done
  echo "ERROR: mime.types not found. Is nginx installed?" >&2
  exit 1
}

generate_conf() {
  local mime_path
  mime_path="$(find_mime_types)"
  cat > "$CONF" <<EOF
worker_processes 1;

events {
    worker_connections 64;
}

http {
    include       $mime_path;
    default_type  application/octet-stream;
    sendfile      on;

    server {
        listen $PORT;
        server_name localhost;

        root $SCRIPT_DIR;

        location = / {
            try_files /index.html =404;
        }

        location /images/ {
            alias $IMAGE_DIR/;
            autoindex on;
            autoindex_format json;
            client_max_body_size 50m;
        }

        location /api/ {
            proxy_pass http://127.0.0.1:$ADB_PORT/;
        }
    }
}
EOF
}

start_adb_helper() {
  python3 "$SCRIPT_DIR/adb_helper.py" &
  echo $! > "$ADB_PID_FILE"
}

stop_adb_helper() {
  if [ -f "$ADB_PID_FILE" ]; then
    kill "$(cat "$ADB_PID_FILE")" 2>/dev/null
    rm -f "$ADB_PID_FILE"
  fi
}

case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))"
      echo "Open: http://localhost:$PORT"
      exit 0
    fi
    echo "Starting slideshow server..."
    start_adb_helper
    generate_conf
    nginx -c "$CONF" -g "pid $PID_FILE;"
    if [ $? -eq 0 ]; then
      echo "Running on http://localhost:$PORT"
      echo "Serving images from: $IMAGE_DIR"
      if [ -n "$SLIDESHOW_CAPTURE_SUFFIX" ]; then
        echo "Phone capture suffix: _$SLIDESHOW_CAPTURE_SUFFIX"
      else
        echo "Phone capture suffix: (none)"
      fi
      echo ""
      echo "Controls:"
      echo "  Space/→  Next image"
      echo "  ←        Previous image"
      echo "  P        Pause"
      echo "  R        Resume"
      echo "  F        Fullscreen"
      echo ""
      echo "Each image change sends: adb shell input keyevent 27"
      echo ""
      echo "Stop with: $0 stop"
    else
      stop_adb_helper
      echo "Failed to start. Is port $PORT in use?"
      exit 1
    fi
    ;;

  stop)
    stop_adb_helper
    if [ -f "$PID_FILE" ]; then
      nginx -c "$CONF" -g "pid $PID_FILE;" -s stop 2>/dev/null
      rm -f "$PID_FILE" "$CONF"
      echo "Stopped."
    else
      echo "Not running."
    fi
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  *)
    echo "Usage: $0 {start|stop|restart}"
    exit 1
    ;;
esac
