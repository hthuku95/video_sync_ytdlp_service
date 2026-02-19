#!/bin/bash
set -e

echo "=== [1/3] Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== [2/3] Cloning bgutil-ytdlp-pot-provider (v1.2.2) ==="
BGUTIL_DIR="$(pwd)/bgutil_server"
if [ -d "$BGUTIL_DIR" ]; then
    echo "  bgutil_server/ already exists, skipping clone"
else
    git clone --single-branch --branch 1.2.2 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        "$BGUTIL_DIR"
fi

echo "=== [3/3] Building bgutil server ==="
cd "$BGUTIL_DIR/server"
npm ci
npx tsc

echo ""
echo "=== Build complete ==="
echo "  Node.js: $(node --version)"
echo "  bgutil server: $BGUTIL_DIR/server/build/main.js"
echo "  yt-dlp plugin: bgutil-ytdlp-pot-provider (installed via pip)"
