#!/bin/bash
set -e

echo "=== [1/4] Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== [2/4] Installing Node.js 20 ==="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
echo "Node.js: $(node --version) | npm: $(npm --version)"

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
echo "  bgutil server: $BGUTIL_DIR/server/build/main.js"
echo "  yt-dlp plugin: bgutil-ytdlp-pot-provider (installed via pip)"
