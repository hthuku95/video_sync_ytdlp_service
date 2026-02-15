# YT-DLP Download Service

FastAPI microservice for reliable YouTube video downloads using yt-dlp. Designed to replace CLI-based yt-dlp subprocess calls in the video_editor backend with a robust HTTP API.

## 🎯 Purpose

This microservice serves as **Strategy #3** in the video_editor's 5-tier YouTube download fallback system:
1. Apify (Primary - expensive)
2. rustube (Fallback #1 - pure Rust)
3. **FastAPI yt-dlp** ⬅️ **THIS SERVICE**
4. rust-yt-downloader (Fallback #3)
5. rusty_ytdl (Fallback #4)

### Why This Exists

- **Eliminates subprocess management** - No PATH issues, reliable in containers
- **Independent deployment** - Scales separately from main backend
- **Anti-bot measures** - Configured user agents, player clients, headers
- **Automatic cleanup** - Files auto-deleted after 5-minute TTL
- **Cost-effective** - Reduces expensive Apify API usage

## 🚀 Quick Start

### Local Development

```bash
# Create virtual environment
python3.12 -m venv env
source env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server
uvicorn app.main:app --reload --port 8000
```

Server runs at: **http://localhost:8000**
API docs: **http://localhost:8000/docs**

### Docker

```bash
# Build image
docker build -t ytdlp-service .

# Run container
docker run -p 8000:8000 \
  -e ALLOWED_ORIGINS="*" \
  -e FILE_TTL_SECONDS=300 \
  ytdlp-service
```

## 📚 API Reference

### 1. POST /api/v1/download

Download YouTube video.

**Request:**
```json
{
  "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
  "job_id": "123",
  "quality": "720p",
  "format": "mp4",
  "prefer_base64": false,
  "timeout_seconds": 3600
}
```

**Response (URL mode):**
```json
{
  "success": true,
  "method": "url",
  "download_url": "/downloads/123/video.mp4",
  "expires_at": "2026-02-15T12:05:00Z",
  "metadata": {
    "title": "Amazing Video",
    "duration_seconds": 1234.56,
    "width": 1280,
    "height": 720,
    "file_size_bytes": 45678901,
    "format": "mp4"
  }
}
```

**Response (Base64 mode):**
```json
{
  "success": true,
  "method": "base64",
  "file_data": "AAAAHGZ0eXBpc29tAAACAGlzb21...",
  "metadata": { /* same as above */ }
}
```

**Error Response:**
```json
{
  "success": false,
  "error": {
    "code": "VIDEO_UNAVAILABLE",
    "message": "Video is private or unavailable",
    "is_transient": false,
    "retry_after_seconds": null,
    "details": {
      "yt_dlp_error": "Original error message"
    }
  }
}
```

**Error Codes:**
- `VIDEO_UNAVAILABLE` (permanent) - Private/deleted/geo-blocked
- `RATE_LIMITED` (transient) - YouTube 429
- `DOWNLOAD_TIMEOUT` (transient) - Exceeded timeout
- `DISK_FULL` (transient) - Server storage full
- `INVALID_URL` (permanent) - Malformed URL
- `NETWORK_ERROR` (transient) - Connection issues
- `SERVER_ERROR` (transient) - Internal error

### 2. POST /api/v1/info

Get video metadata without downloading.

**Request:**
```json
{
  "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
  "include_formats": false
}
```

**Response:**
```json
{
  "success": true,
  "metadata": {
    "video_id": "dQw4w9WgXcQ",
    "title": "Amazing Video",
    "duration_seconds": 1234.56,
    "channel_id": "UC...",
    "channel_name": "Channel Name",
    "upload_date": "20260215",
    "view_count": 1000000,
    "like_count": 50000,
    "is_live": false,
    "is_private": false
  }
}
```

### 3. GET /api/v1/health

Health check for monitoring.

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 12345,
  "stats": {
    "total_downloads": 1234,
    "active_downloads": 3,
    "failed_downloads": 45,
    "disk_usage_percent": 42.5
  },
  "yt_dlp_version": "2024.02.15"
}
```

### 4. GET /downloads/{job_id}/{filename}

Serve downloaded file (auto-generated URLs from /api/v1/download).

**Features:**
- 5-minute expiration (configurable via `FILE_TTL_SECONDS`)
- Auto-cleanup after expiration
- Returns 404 if file not found
- Returns 410 if file expired

## 🔧 Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Server port |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |
| `DOWNLOADS_DIR` | `/tmp/downloads` | Download storage directory |
| `FILE_TTL_SECONDS` | `300` | File expiration time (5 minutes) |
| `CLEANUP_INTERVAL_SECONDS` | `60` | Cleanup scheduler interval |
| `LOG_LEVEL` | `INFO` | Logging level |

## 🏗️ Architecture

### File Structure
```
YTDLPAPI/
├── app/
│   ├── __init__.py          # Package metadata
│   ├── main.py              # FastAPI app, endpoints, CORS
│   ├── downloader.py        # yt-dlp wrapper with anti-bot measures
│   ├── storage.py           # File management, cleanup scheduler
│   └── models.py            # Pydantic request/response schemas
├── requirements.txt          # Python dependencies
├── Dockerfile               # Container definition
├── .env.example             # Environment variables template
├── .gitignore               # Git ignore patterns
└── README.md                # This file
```

### Key Components

**downloader.py:**
- Wraps yt-dlp with production-ready configuration
- Anti-bot measures (user agents, player clients, headers)
- Error classification (transient vs permanent)
- Quality/format selection
- Timeout handling

**storage.py:**
- File management with job-based organization
- Background cleanup scheduler (runs every 60s)
- Automatic TTL-based file deletion
- Disk usage monitoring

**models.py:**
- Type-safe request/response schemas
- Error code enumerations
- Metadata structures

## 📦 Deployment

### Render.com

1. **Create Web Service:**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Instance Type: Starter (512MB RAM, 0.5 CPU)

2. **Environment Variables:**
   ```
   ALLOWED_ORIGINS=https://videosync.video,https://cmachine.devthuku.io
   FILE_TTL_SECONDS=300
   DOWNLOADS_DIR=/tmp/downloads
   ```

3. **Health Check:**
   - Path: `/api/v1/health`
   - Interval: 30s

### Other Platforms

**Heroku:**
```bash
heroku create ytdlp-service
git push heroku main
```

**Railway:**
```bash
railway up
```

**Fly.io:**
```bash
fly launch
fly deploy
```

## 🔍 Monitoring

### Logs

```bash
# Local
tail -f app.log

# Docker
docker logs -f <container_id>

# Render
# View in Render Dashboard → Logs
```

### Key Log Events

- `📥 Download request:` - New download started
- `✅ Download URL:` - Download succeeded (URL mode)
- `✅ Encoding file as base64:` - Download succeeded (base64 mode)
- `❌ Download failed:` - Download failed
- `⚠️ File expired:` - Attempted to access expired file
- `Cleaned up expired job:` - Cleanup scheduler removed old files

### Metrics to Monitor

1. **Success Rate:** `total_downloads / (total_downloads + failed_downloads)`
2. **Active Downloads:** Should stay low (<10)
3. **Disk Usage:** Alert if >80%
4. **Response Time:** Monitor /api/v1/health latency

## 🧪 Testing

### Manual Testing

```bash
# Test info endpoint
curl -X POST http://localhost:8000/api/v1/info \
  -H "Content-Type: application/json" \
  -d '{"video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}'

# Test download endpoint
curl -X POST http://localhost:8000/api/v1/download \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "job_id": "test_123",
    "quality": "360p"
  }'

# Download file (use URL from response)
curl -O http://localhost:8000/downloads/test_123/video.mp4

# Health check
curl http://localhost:8000/api/v1/health
```

### Integration with video_editor

See Rust integration client: `src/clipping/ytdlp_api_client.rs`

## 🐛 Troubleshooting

### "Video is private or unavailable"
- **Cause:** Video is actually private, deleted, or geo-blocked
- **Solution:** Not retryable, return error to user

### "Rate limited by YouTube"
- **Cause:** Too many requests from same IP
- **Solution:** Wait 5 minutes, use Apify fallback

### "Download timed out"
- **Cause:** Large video, slow connection
- **Solution:** Increase `timeout_seconds` in request

### "Server disk full"
- **Cause:** Cleanup scheduler not running or too many active downloads
- **Solution:** Check cleanup scheduler, increase `CLEANUP_INTERVAL_SECONDS`, or provision more disk space

### yt-dlp extraction errors
- **Cause:** YouTube changed their API/HTML structure
- **Solution:** Update yt-dlp: `pip install --upgrade yt-dlp`

## 🔒 Security

### CORS Configuration

Restrict `ALLOWED_ORIGINS` in production:
```
ALLOWED_ORIGINS=https://videosync.video,https://cmachine.devthuku.io
```

### Rate Limiting (TODO)

Consider adding rate limiting middleware:
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@app.post("/api/v1/download")
@limiter.limit("10/minute")
async def download_video(...):
    ...
```

### Authentication (TODO)

For production, add API key authentication:
```python
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("API_KEY"):
        raise HTTPException(status_code=403)
```

## 📊 Performance

### Benchmarks (Local Testing)

| Quality | Video Length | Download Time | File Size |
|---------|--------------|---------------|-----------|
| 360p | 5 min | ~15s | ~25 MB |
| 720p | 5 min | ~30s | ~75 MB |
| 1080p | 5 min | ~60s | ~150 MB |

### Optimization Tips

1. **Parallel downloads:** Increase Render instance size for more concurrent downloads
2. **CDN integration:** Use Cloudflare R2/S3 for file serving
3. **Caching:** Cache popular video metadata
4. **Streaming:** Stream bytes directly to client (avoid disk writes)

## 🚦 Roadmap

**v1.1 (Short-term):**
- [ ] Rate limiting middleware
- [ ] API key authentication
- [ ] Prometheus metrics endpoint
- [ ] WebSocket progress updates

**v2.0 (Long-term):**
- [ ] CDN integration (Cloudflare R2)
- [ ] Regional deployment (multi-region)
- [ ] Video format conversion
- [ ] Playlist support
- [ ] Thumbnail extraction

## 📄 License

MIT License - See video_editor main repository

## 🤝 Contributing

This is a microservice for the video_editor project. For issues or feature requests, see:
- Main repo: https://github.com/hthuku95/video_sync
- Issues: https://github.com/hthuku95/video_sync/issues

## 📞 Support

For production issues:
1. Check logs in Render Dashboard
2. Verify health endpoint: `curl https://ytdlp-service.render.com/api/v1/health`
3. Test with curl (see Testing section)
4. Check yt-dlp version compatibility

---

**Built for video_editor backend** | FastAPI + yt-dlp | Production-ready
