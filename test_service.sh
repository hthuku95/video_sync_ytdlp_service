#!/bin/bash
# Quick test script for FastAPI yt-dlp service

set -e

BASE_URL="${1:-http://localhost:8000}"

echo "🧪 Testing FastAPI yt-dlp service at: $BASE_URL"
echo ""

# Test 1: Health check
echo "1️⃣ Testing /api/v1/health..."
curl -s "$BASE_URL/api/v1/health" | jq .
echo "✅ Health check passed"
echo ""

# Test 2: Info endpoint (Rick Astley - Never Gonna Give You Up)
echo "2️⃣ Testing /api/v1/info..."
curl -s -X POST "$BASE_URL/api/v1/info" \
  -H "Content-Type: application/json" \
  -d '{"video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}' | jq .
echo "✅ Info endpoint passed"
echo ""

# Test 3: Download endpoint (short video for testing)
echo "3️⃣ Testing /api/v1/download (this may take a minute)..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/download" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "job_id": "test_'$(date +%s)'",
    "quality": "360p"
  }')

echo "$RESPONSE" | jq .

# Check if download URL is present
if echo "$RESPONSE" | jq -e '.download_url' > /dev/null; then
  DOWNLOAD_URL=$(echo "$RESPONSE" | jq -r '.download_url')
  echo "✅ Download endpoint passed"
  echo "📥 Download URL: $BASE_URL$DOWNLOAD_URL"

  # Test 4: Download the file
  echo ""
  echo "4️⃣ Testing file download..."
  curl -s -o /tmp/test_video.mp4 "$BASE_URL$DOWNLOAD_URL"

  if [ -f /tmp/test_video.mp4 ] && [ $(stat -f%z /tmp/test_video.mp4 2>/dev/null || stat -c%s /tmp/test_video.mp4) -gt 1000 ]; then
    echo "✅ File download passed ($(stat -f%z /tmp/test_video.mp4 2>/dev/null || stat -c%s /tmp/test_video.mp4) bytes)"
    rm /tmp/test_video.mp4
  else
    echo "❌ File download failed"
    exit 1
  fi
else
  echo "❌ Download endpoint failed - no download_url in response"
  exit 1
fi

echo ""
echo "🎉 All tests passed!"
