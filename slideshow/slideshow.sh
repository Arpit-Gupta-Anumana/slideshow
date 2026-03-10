#!/bin/bash
# Slideshow server manager

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="/tmp/slideshow-nginx.conf"
PID_FILE="/tmp/slideshow-nginx.pid"
PORT=8899

# Find mime.types across common nginx install locations
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
            alias $SCRIPT_DIR/images/;
            autoindex on;
            autoindex_format json;
            client_max_body_size 50m;
        }
    }
}
EOF
}

case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))"
      echo "Open: http://localhost:$PORT"
      exit 0
    fi
    echo "Starting slideshow server..."
    generate_conf
    nginx -c "$CONF" -g "pid $PID_FILE;"
    if [ $? -eq 0 ]; then
      echo "Running on http://localhost:$PORT"
      echo "Put your images in: $SCRIPT_DIR/images/"
      echo ""
      echo "Controls:"
      echo "  Space/→  Next image"
      echo "  ←        Previous image"
      echo "  P/K      Pause / Resume"
      echo "  Dbl-click  Fullscreen"
      echo ""
      echo "Stop with: $0 stop"
    else
      echo "Failed to start. Is port $PORT in use?"
      exit 1
    fi
    ;;

  stop)
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
