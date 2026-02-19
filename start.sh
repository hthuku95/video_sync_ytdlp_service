#!/bin/bash
BGUTIL_DIR="$(pwd)/bgutil_server"
BGUTIL_BIN="$BGUTIL_DIR/server/build/main.js"

if [ -f "$BGUTIL_BIN" ]; then
    echo "=== Starting bgutil PO token server on port 4416 ==="
    node "$BGUTIL_BIN" --port 4416 &
    BGUTIL_PID=$!
    echo "  bgutil started (PID: $BGUTIL_PID)"
    sleep 3
    echo "  bgutil ready — YouTube PO token generation active"
else
    echo "WARNING: bgutil server not found at $BGUTIL_BIN — PO tokens unavailable"
fi

echo "=== Starting uvicorn ==="
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
