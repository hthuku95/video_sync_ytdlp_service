#!/bin/bash
set -e

echo "=== [1/4] Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== [2/4] Installing Playwright browsers ==="
# Store browsers inside the project directory so Render's build cache captures them.
# Default ~/.cache/ms-playwright/ is outside the cached tree and is lost between deploys.
export PLAYWRIGHT_BROWSERS_PATH="$(pwd)/.playwright-browsers"
playwright install chromium
echo "  Playwright browsers installed at: $PLAYWRIGHT_BROWSERS_PATH"
ls "$PLAYWRIGHT_BROWSERS_PATH"

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
