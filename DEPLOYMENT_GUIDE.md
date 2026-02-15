# FastAPI yt-dlp Service - Deployment Guide

Complete guide for deploying the FastAPI yt-dlp microservice and integrating it with the video_editor backend.

## 📋 Pre-Deployment Checklist

- [x] Part A: FastAPI microservice implemented
- [x] Part B: Rust integration client implemented
- [x] Compilation successful (`cargo check`)
- [x] Environment variables configured
- [ ] Local testing completed
- [ ] Git repository initialized
- [ ] Service deployed to Render.com
- [ ] Production environment updated
- [ ] Integration verified

---

## 🚀 Phase 1: Local Testing

### 1. Install Python Dependencies

```bash
cd VideoSyncIntegrations/YTDLPAPI
python3.12 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### 2. Start FastAPI Service

```bash
# Terminal 1: Run FastAPI service
uvicorn app.main:app --reload --port 8000

# Server should start at: http://localhost:8000
# API docs available at: http://localhost:8000/docs
```

### 3. Test Endpoints

```bash
# Terminal 2: Run test script
./test_service.sh

# Or manually test:
curl http://localhost:8000/api/v1/health | jq .
```

Expected output:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 12.345,
  "stats": {
    "total_downloads": 0,
    "active_downloads": 0,
    "failed_downloads": 0,
    "disk_usage_percent": 42.5
  },
  "yt_dlp_version": "2024.02.15"
}
```

### 4. Test Rust Integration

```bash
# Terminal 3: Run Rust backend
cd ../../..  # Back to video_editor root
cargo run

# Server should start at: http://localhost:3000
# Check logs for: "YtdlpApiClient initialized with base_url: http://localhost:8000"
```

### 5. Trigger Test Download

**Option A: Via Admin Dashboard**
1. Open http://localhost:3000/admin/clipping-jobs
2. Create a test clipping job
3. Watch logs for "Strategy 3 (FastAPI microservice)" messages

**Option B: Via API**
```bash
# Create test job (requires authentication)
curl -X POST http://localhost:3000/api/clipping/jobs \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "youtube_source_channel_id": 1
  }'
```

**Expected Logs:**
```
🔄 Trying Strategy 1 (Apify - paid service)...
⚠️ Strategy 1 (Apify) failed: ...
🔄 Trying Strategy 2 (rustube - pure Rust)...
⚠️ Strategy 2 (rustube) failed: ...
🔄 Trying Strategy 3 (FastAPI yt-dlp microservice)...
🌐 YtdlpApiClient::download_video starting: ...
📤 POST http://localhost:8000/api/v1/download
📥 Response status: 200
📦 Download method: url
📥 Downloading from: http://localhost:8000/downloads/...
💾 Writing 45678901 bytes to /path/to/output.mp4
✅ File written successfully: 45678901 bytes
✅ Strategy 3 (FastAPI microservice) succeeded
```

---

## 🌐 Phase 2: Deploy FastAPI Microservice to Render.com

### 1. Initialize Git Repository

```bash
cd VideoSyncIntegrations/YTDLPAPI

# Initialize repository
git init
git add .
git commit -m "Initial commit: FastAPI yt-dlp microservice v1.0.0"

# Create GitHub repository
gh repo create video_sync_ytdlp_service --public --source=. --remote=origin

# Or manually push using GITHUB_TOKEN from .env
git remote add origin https://github.com/hthuku95/video_sync_ytdlp_service.git
git branch -M main
git push -u origin main
```

### 2. Create Render.com Web Service

**Via Render Dashboard:**

1. **Login to Render.com**
   - URL: https://dashboard.render.com
   - Use GitHub account

2. **Create New Web Service**
   - Click "New +" → "Web Service"
   - Connect GitHub repository: `hthuku95/video_sync_ytdlp_service`
   - Or use "Public Git Repository" URL: `https://github.com/hthuku95/video_sync_ytdlp_service`

3. **Configure Service**
   ```
   Name:              ytdlp-service
   Region:            Oregon (US West) - same as video_editor backend
   Branch:            main
   Root Directory:    (leave blank)
   Runtime:           Python 3
   Build Command:     pip install -r requirements.txt
   Start Command:     uvicorn app.main:app --host 0.0.0.0 --port $PORT
   Instance Type:     Starter (512MB RAM, 0.5 CPU shared) - $7/month
   ```

4. **Environment Variables**
   ```
   ALLOWED_ORIGINS=https://videosync.video,https://cmachine.devthuku.io
   FILE_TTL_SECONDS=300
   DOWNLOADS_DIR=/tmp/downloads
   CLEANUP_INTERVAL_SECONDS=60
   LOG_LEVEL=INFO
   ```

5. **Advanced Settings**
   ```
   Auto-Deploy:        Yes
   Health Check Path:  /api/v1/health
   Docker Command:     (leave blank, use Start Command)
   ```

6. **Click "Create Web Service"**

### 3. Wait for Deployment

**Deployment takes ~5-10 minutes:**
- Building Docker image
- Installing dependencies
- Starting service
- Running health checks

**Monitor deployment:**
- Render Dashboard → ytdlp-service → Logs
- Look for: "🚀 Starting yt-dlp download service..."

### 4. Verify Deployment

```bash
# Get service URL (e.g., https://ytdlp-service.onrender.com)
SERVICE_URL="https://ytdlp-service.onrender.com"

# Test health endpoint
curl "$SERVICE_URL/api/v1/health" | jq .

# Should return:
# {
#   "status": "healthy",
#   "version": "1.0.0",
#   ...
# }
```

### 5. Note the Service URL

**Production URL will be:**
```
https://ytdlp-service.onrender.com
```

**Save this URL - you'll need it for Phase 3!**

---

## 🔗 Phase 3: Update video_editor Backend

### 1. Update Production Environment Variables

**Via Render Dashboard (video_editor backend):**

1. Go to: https://dashboard.render.com
2. Select: `video-editor-backend` service
3. Navigate to: "Environment" tab
4. Add/Update variable:
   ```
   YTDLP_API_URL=https://ytdlp-service.onrender.com
   ```
5. Click "Save Changes"
6. Service will auto-restart (~2-3 minutes)

**Or via Render.yaml (if using infrastructure-as-code):**

```yaml
# render.yaml
services:
  - type: web
    name: video-editor-backend
    env:
      - key: YTDLP_API_URL
        value: https://ytdlp-service.onrender.com
```

### 2. Commit and Push Rust Changes

```bash
cd /home/harry/projects/DevThukuDotIO/Rust/video_editor

# Check current status
git status

# Stage changes
git add src/clipping/ytdlp_api_client.rs
git add src/clipping/mod.rs
git add src/clipping/apify_client.rs
git add .env
git add .env.test

# Commit
git commit -m "FEATURE: Integrate FastAPI yt-dlp microservice (Strategy #3)

- Add YtdlpApiClient HTTP client (src/clipping/ytdlp_api_client.rs)
- Replace yt-dlp CLI with FastAPI microservice in fallback chain
- Update module exports and imports
- Configure YTDLP_API_URL environment variable
- Improves download reliability and eliminates subprocess issues

Related: FastAPI microservice deployed at ytdlp-service.onrender.com"

# Push to trigger auto-deployment
git push origin master
```

### 3. Monitor Deployment

**Render Dashboard → video-editor-backend → Logs**

Look for:
```
✅ Build successful
🚀 Starting server...
YtdlpApiClient initialized with base_url: https://ytdlp-service.onrender.com
Server running at 0.0.0.0:3000
```

**Deployment ETA:** 5-10 minutes

---

## ✅ Phase 4: Verification & Testing

### 1. Check Production Logs

**Render Dashboard → video-editor-backend → Logs**

Filter for "Strategy 3" messages:
```bash
# Should see:
🔄 Trying Strategy 3 (FastAPI yt-dlp microservice)...
🌐 YtdlpApiClient::download_video starting: ...
✅ Strategy 3 (FastAPI microservice) succeeded
```

### 2. Test via Admin Dashboard

1. **Open Production Admin Dashboard:**
   - URL: https://videosync.video/admin/clipping-jobs
   - Login as superuser

2. **Check Pending Jobs:**
   - View list of 152 pending jobs
   - Watch for jobs transitioning to "processing"

3. **Monitor Job Progress:**
   - Refresh every 30 seconds
   - Jobs should complete within 5-15 minutes each
   - Success rate should be >70% (up from current ~0%)

### 3. Verify Strategy #3 Usage

**Check microservice logs:**
```bash
# Render Dashboard → ytdlp-service → Logs

# Should see requests:
📥 Download request: https://youtube.com/watch?v=... (job_id=123, quality=720p)
✅ Download URL: /downloads/123/video.mp4 (expires: ...)
```

**Count successful downloads:**
```bash
curl https://ytdlp-service.onrender.com/api/v1/health | jq '.stats'

# Example:
# {
#   "total_downloads": 45,
#   "active_downloads": 2,
#   "failed_downloads": 8,
#   "disk_usage_percent": 12.3
# }
```

### 4. Compare Success Rates

**Before (Strategy #3 = yt-dlp CLI):**
- Total jobs: 169
- Completed: 0 (0%)
- Failed: 14
- Pending: 152

**After (Strategy #3 = FastAPI microservice) - Target:**
- Completion rate: >70%
- Strategy #3 success rate: >70%
- Pending backlog: <100 within 24 hours
- Failed jobs: <30

---

## 📊 Phase 5: Monitoring & Optimization

### Key Metrics to Track

**1. Download Strategy Success Rates**

Track which strategies are used most:
```
Strategy 1 (Apify):          15% success, 85% failed
Strategy 2 (rustube):        5% success, 95% failed
Strategy 3 (FastAPI yt-dlp): 75% success, 25% failed  ⬅️ TARGET
Strategy 4 (rust-yt-dl):     10% success, 90% failed
Strategy 5 (rusty_ytdl):     5% success, 95% failed
```

**2. Cost Optimization**

Calculate Apify cost savings:
```
Before: 100% of downloads via Apify = $0.30 × 100 = $30/100 videos
After:  20% via Apify, 75% via FastAPI = $0.30 × 20 = $6/100 videos
Savings: $24 per 100 videos (80% cost reduction)
```

**3. Performance Metrics**

Average download time per strategy:
```
Apify:          45s
rustube:        30s (when it works)
FastAPI yt-dlp: 60s  ⬅️ Acceptable
rust-yt-dl:     90s
rusty_ytdl:     120s
```

**4. Error Patterns**

Monitor new error types from FastAPI service:
```bash
# Check error codes
curl https://ytdlp-service.onrender.com/api/v1/health | jq '.stats'

# Review failed jobs
# Render Dashboard → video-editor-backend → Logs
# Filter: "Strategy 3.*failed"
```

### Health Check Alerts

**Set up Render.com alerts:**

1. Render Dashboard → ytdlp-service → "Alerts"
2. Configure:
   ```
   Health Check Failed: Alert after 3 failures
   High CPU Usage:      Alert above 80%
   High Memory Usage:   Alert above 400MB (of 512MB)
   Disk Full:           Alert above 80%
   ```

### Scaling Recommendations

**If download volume increases:**

**Option 1: Vertical Scaling (Render.com)**
```
Starter:     512MB RAM, 0.5 CPU - $7/month  (current)
Standard:    2GB RAM, 1 CPU     - $25/month (4x capacity)
Pro:         4GB RAM, 2 CPU     - $85/month (8x capacity)
```

**Option 2: Horizontal Scaling**
- Deploy multiple instances
- Add load balancer
- Round-robin requests

**Option 3: CDN Integration**
- Upload to Cloudflare R2 / S3
- Return CDN URLs instead of direct downloads
- Reduces bandwidth 2x (no proxy through Rust backend)

---

## 🐛 Troubleshooting Guide

### Issue 1: "YTDLP_API_URL environment variable not set"

**Symptom:**
```
⚠️ Strategy 3 (FastAPI microservice) failed: YTDLP_API_URL environment variable not set
```

**Solution:**
1. Verify environment variable in Render Dashboard
2. Restart backend service
3. Check for typos (should be `YTDLP_API_URL`, not `YTDLP_SERVICE_URL`)

### Issue 2: "HTTP request failed: connection refused"

**Symptom:**
```
⚠️ Strategy 3 (FastAPI microservice) failed: HTTP request failed: connection refused
```

**Root Cause:** FastAPI service not running or wrong URL

**Solution:**
1. Check microservice health: `curl https://ytdlp-service.onrender.com/api/v1/health`
2. Verify YTDLP_API_URL is correct
3. Check microservice logs for crashes
4. Restart microservice if needed

### Issue 3: "HTTP 500 error: transient"

**Symptom:**
```
⚠️ Strategy 3 (FastAPI microservice) failed: HTTP 500 error (transient): Download failed
```

**Root Cause:** yt-dlp extraction errors

**Solution:**
1. Check microservice logs for yt-dlp errors
2. Update yt-dlp: `pip install --upgrade yt-dlp` and redeploy
3. Verify video URL is accessible
4. Check for rate limiting (429 errors)

### Issue 4: "File download failed with status: 404"

**Symptom:**
```
❌ File download failed with status: 404
```

**Root Cause:** File expired or cleanup ran too early

**Solution:**
1. Increase FILE_TTL_SECONDS (default: 300 = 5 minutes)
2. Adjust CLEANUP_INTERVAL_SECONDS if needed
3. Check disk space on microservice

### Issue 5: High Failure Rate (>50%)

**Symptom:** Strategy #3 success rate <50%

**Investigation:**
```bash
# Check microservice stats
curl https://ytdlp-service.onrender.com/api/v1/health | jq '.stats'

# Check error patterns in logs
# Render Dashboard → ytdlp-service → Logs
# Look for recurring error codes
```

**Common Causes:**
1. **Rate Limiting** - Reduce concurrent requests
2. **Outdated yt-dlp** - Update and redeploy
3. **YouTube API changes** - Update yt-dlp, adjust user agents
4. **Disk Full** - Increase cleanup frequency or provision more disk

---

## 🔄 Rollback Plan

If FastAPI microservice causes issues, rollback in 5 minutes:

### Quick Rollback (5 minutes)

```bash
# Option 1: Disable via environment variable
# Render Dashboard → video-editor-backend → Environment
# Comment out or remove: YTDLP_API_URL
# Service auto-restarts, Strategy #3 skips microservice

# Option 2: Revert code changes
cd /home/harry/projects/DevThukuDotIO/Rust/video_editor
git log --oneline -5
git revert <commit-hash>
git push origin master
```

### Full Rollback (30 minutes)

```bash
# Restore old Strategy #3 (yt-dlp CLI)
cd src/clipping
git checkout HEAD~1 apify_client.rs
git checkout HEAD~1 mod.rs
rm ytdlp_api_client.rs

git commit -m "ROLLBACK: Restore yt-dlp CLI as Strategy #3"
git push origin master
```

---

## 📚 Additional Resources

**Documentation:**
- FastAPI microservice README: `VideoSyncIntegrations/YTDLPAPI/README.md`
- Implementation plan: `YTDLP_MICROSERVICE_PLAN.md`
- Test strategy: `tests/ADMIN_TEST_STRATEGY.md`

**Monitoring:**
- Render Dashboard: https://dashboard.render.com
- Production API: https://videosync.video
- Microservice API: https://ytdlp-service.onrender.com
- Health endpoint: https://ytdlp-service.onrender.com/api/v1/health

**Support:**
- GitHub Issues: https://github.com/hthuku95/video_sync/issues
- Production logs: Render Dashboard → Logs

---

## ✅ Success Checklist

**Week 1 (Feb 15-22, 2026):**
- [ ] FastAPI microservice deployed and healthy
- [ ] Rust integration deployed to production
- [ ] Strategy #3 success rate >70%
- [ ] No increase in overall job failure rate
- [ ] Pending backlog reduces (147 → <100)
- [ ] Cost savings visible (reduced Apify usage)

**Week 2 (Feb 22-29, 2026):**
- [ ] Success rate stable at >70%
- [ ] All 152 pending jobs processed
- [ ] New jobs complete within 15 minutes
- [ ] Zero new error patterns
- [ ] Monitoring alerts configured
- [ ] Documentation updated with production learnings

**Month 1 (Feb-Mar 2026):**
- [ ] 95% job success rate
- [ ] <5% jobs using Apify (cost optimized)
- [ ] Average job completion time <10 minutes
- [ ] Zero infinite retry loops
- [ ] CDN integration scoped (optional future work)

---

**Deployment Status:** ⏳ Ready for Phase 1 (Local Testing)

**Next Step:** Run `./test_service.sh` to test FastAPI microservice locally
