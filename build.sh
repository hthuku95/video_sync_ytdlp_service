#!/bin/bash
set -e

echo "=== [1/4] Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== [2/4] Installing Playwright browsers ==="
playwright install --with-deps chromium
# --with-deps installs OS-level system libraries (libnss3, libgbm1, etc.)
# Required on Render's native Python runtime (not Docker) where apt deps may be absent

echo "=== [3/4] Cloning bgutil-ytdlp-pot-provider (v1.2.2) ==="
BGUTIL_DIR="$(pwd)/bgutil_server"
if [ -d "$BGUTIL_DIR" ]; then
    echo "  bgutil_server/ already exists, skipping clone"
else
    git clone --single-branch --branch 1.2.2 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        "$BGUTIL_DIR"
fi

echo "=== [4/4] Building bgutil server ==="
cd "$BGUTIL_DIR/server"
npm ci
npx tsc

echo ""
echo "=== Build complete ==="
echo "  Node.js: $(node --version)"
echo "  bgutil server: $BGUTIL_DIR/server/build/main.js"
echo "  yt-dlp plugin: bgutil-ytdlp-pot-provider (installed via pip)"
