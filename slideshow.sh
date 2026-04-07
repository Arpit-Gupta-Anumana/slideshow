#!/bin/bash
# Slideshow server manager (unified)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="/tmp/slideshow-nginx.conf"
PID_FILE="/tmp/slideshow-nginx.pid"
ADB_PID_FILE="/tmp/slideshow-adb-helper.pid"
LOG_DIR="${LOG_DIR:-/tmp}"

# ── Image source ──
IMAGE_DIR="${SLIDESHOW_IMAGES:-$SCRIPT_DIR/images}"

# ── Capture naming ──
export SLIDESHOW_FOLDER_LABEL="${SLIDESHOW_FOLDER_LABEL:-}"
export SLIDESHOW_CAPTURE_SUFFIX="${SLIDESHOW_CAPTURE_SUFFIX:-}"

# ── Capture mode: 1 = browser waits for phone (recommended), 0 = fire-and-forget ──
export SLIDESHOW_SYNC_CAPTURE="${SLIDESHOW_SYNC_CAPTURE:-1}"

# ── Local pull destination (empty = don't pull) ──
CAPTURE_DEST="${SLIDESHOW_CAPTURE:-}"
export CAPTURE_DIR="$CAPTURE_DEST"

# ── Remote rsync (empty = disabled) ──
export REMOTE_HOST="${REMOTE_HOST:-}"
export REMOTE_PATH="${REMOTE_PATH:-}"
export SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
export BATCH_SIZE="${BATCH_SIZE:-1000}"

# ── Logging ──
export LOG_DIR="${LOG_DIR}"
export SLIDESHOW_CAPTURE_LOG="${SLIDESHOW_CAPTURE_LOG:-$LOG_DIR/capture_audit.jsonl}"

PORT=8899
ADB_PORT=8900

# Bundled adb support
if [ -x "$SCRIPT_DIR/.tools/platform-tools/adb" ]; then
  export PATH="$SCRIPT_DIR/.tools/platform-tools:$PATH"
fi

find_mime_types() {
  for f in /opt/homebrew/etc/nginx/mime.types /usr/local/etc/nginx/mime.types /etc/nginx/mime.types /usr/share/nginx/mime.types; do
    [ -f "$f" ] && echo "$f" && return
  done
  echo "ERROR: mime.types not found. Is nginx installed?" >&2
  exit 1
}

generate_conf() {
  local mime_path
  mime_path="$(find_mime_types)" || exit 1
  cat > "$CONF" <<EOF
error_log $LOG_DIR/slideshow-nginx-error.log;
worker_processes 1;

events {
    worker_connections 64;
}

http {
    include       $mime_path;
    default_type  application/octet-stream;
    sendfile      on;
    access_log    $LOG_DIR/slideshow-nginx-access.log;
    error_log     $LOG_DIR/slideshow-nginx-error.log;

    server {
        listen $PORT;
        server_name localhost;

        root $SCRIPT_DIR;

        location = / {
            try_files /index.html =404;
        }

        location /images/ {
            alias "$IMAGE_DIR/";
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
    nginx -c "$CONF" -g "pid $PID_FILE; error_log $LOG_DIR/slideshow-nginx-error.log;"
    if [ $? -eq 0 ]; then
      echo ""
      echo "Running on http://localhost:$PORT"
      echo "Serving images from: $IMAGE_DIR"
      echo ""
      # Naming info
      NAME_PARTS="<stem>"
      [ -n "$SLIDESHOW_FOLDER_LABEL" ] && NAME_PARTS="${NAME_PARTS}_${SLIDESHOW_FOLDER_LABEL}"
      [ -n "$SLIDESHOW_CAPTURE_SUFFIX" ] && NAME_PARTS="${NAME_PARTS}_${SLIDESHOW_CAPTURE_SUFFIX}"
      echo "Rename pattern: ${NAME_PARTS}<ext>"
      echo "Capture mode: $([ "$SLIDESHOW_SYNC_CAPTURE" = "1" ] && echo 'sync (browser waits)' || echo 'async (fire-and-forget)')"
      # Pull/sync info
      if [ -n "$CAPTURE_DEST" ]; then
        echo "Pull to local: $CAPTURE_DEST"
      else
        echo "Pull to local: OFF"
      fi
      if [ -n "$REMOTE_HOST" ] && [ -n "$REMOTE_PATH" ]; then
        echo "Remote sync: every $BATCH_SIZE files -> $REMOTE_HOST:$REMOTE_PATH"
      else
        echo "Remote sync: OFF"
      fi
      echo ""
      echo "Controls:"
      echo "  Space/→  Next image"
      echo "  ←        Previous image"
      echo "  P        Pause"
      echo "  R        Resume"
      echo "  F        Fullscreen"
      echo ""
      echo "Resume from image: http://localhost:$PORT/?start=<number>"
      echo "Force sync:        curl -X POST http://localhost:$PORT/api/sync"
      echo "View stats:        curl http://localhost:$PORT/api/stats"
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
      nginx -c "$CONF" -g "pid $PID_FILE; error_log $LOG_DIR/slideshow-nginx-error.log;" -s stop 2>/dev/null
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
