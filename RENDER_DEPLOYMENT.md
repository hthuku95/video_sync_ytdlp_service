# Render.com Deployment Checklist

**Repository:** https://github.com/hthuku95/video_sync_ytdlp_service
**Date:** February 15, 2026
**Status:** Ready for deployment

---

## ✅ Pre-Deployment Checklist

- [x] Code pushed to GitHub
- [x] render.yaml configuration file created
- [x] Local testing completed (all tests passed)
- [x] Documentation complete
- [ ] Deployed to Render.com
- [ ] Production URL obtained
- [ ] Service verified (health check)
- [ ] Backend environment updated

---

## 🚀 Quick Deployment Steps

### Step 1: Deploy to Render.com (5 minutes)

1. **Go to:** https://dashboard.render.com
2. **Click:** "New +" → "Blueprint" (Recommended)
3. **Connect:** GitHub repository `video_sync_ytdlp_service`
4. **Click:** "Apply" (render.yaml will auto-configure everything)
5. **Wait:** 5-10 minutes for deployment

**Alternative (Manual):** Use "Web Service" instead of "Blueprint"

### Step 2: Get Production URL (1 minute)

After deployment completes:
- Find URL in Render Dashboard
- Format: `https://ytdlp-service.onrender.com` or `https://ytdlp-service-XXXXX.onrender.com`
- **Save this URL!**

### Step 3: Test Deployment (2 minutes)

```bash
# Replace with your actual URL
export SERVICE_URL="https://ytdlp-service.onrender.com"

# Test health endpoint
curl "$SERVICE_URL/api/v1/health"

# Should return:
# {"status":"healthy","version":"1.0.0",...}
```

### Step 4: Update Backend Environment (3 minutes)

1. Go to Render Dashboard
2. Select: `video-editor-backend` service
3. Go to: Environment tab
4. Update: `YTDLP_API_URL=https://ytdlp-service-9eae.onrender.com`
5. Click: "Save Changes"
6. Wait: 2-3 minutes for restart

---

## 🔧 Configuration Details

### Service Settings (from render.yaml)

```yaml
Name:              ytdlp-service
Region:            Oregon (US West)
Runtime:           Python 3
Plan:              Starter ($7/month, 512MB RAM, 0.5 CPU)
Build Command:     pip install -r requirements.txt
Start Command:     uvicorn app.main:app --host 0.0.0.0 --port $PORT
Health Check:      /api/v1/health
Auto-Deploy:       Yes (on push to main branch)
```

### Environment Variables

```
ALLOWED_ORIGINS=https://videosync.video,https://cmachine.devthuku.io
FILE_TTL_SECONDS=300
DOWNLOADS_DIR=/tmp/downloads
CLEANUP_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
```

---

## 🍪 Setting Up YouTube Cookies (Required for Production)

YouTube blocks downloads from datacenter IPs without authentication cookies. Without cookies, you will see:
```
ERROR: Sign in to confirm you're not a bot.
WARNING: HTTP Error 429: Too Many Requests
```

### Step 1: Export Cookies from Chrome

1. Open **Chrome** and open an **Incognito window** (`Ctrl+Shift+N`)
2. Navigate to `https://www.youtube.com` and **sign in** with a Google account
3. Install the [**Get cookies.txt LOCALLY**](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) Chrome extension
4. While on the YouTube tab, click the extension icon
5. Select **Export** → choose `youtube.com` (not all sites)
6. Save the file as `cookies.txt`

### Step 2: Base64-Encode the Cookies File

**Linux / Mac:**
```bash
base64 -w 0 cookies.txt
# Copy the entire output (it will be a long single line)
```

**Windows (PowerShell):**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))
```

### Step 3: Add Cookie to Render.com

1. Go to **https://dashboard.render.com**
2. Select your **ytdlp-service** web service
3. Click the **Environment** tab
4. Click **Add Environment Variable**
5. Set:
   - **Key:** `YTDLP_COOKIES_B64`
   - **Value:** *(paste the base64 output from Step 2)*
6. Click **Save Changes**
7. The service will automatically redeploy (~2-3 minutes)

### Step 4: Verify Cookies Are Loaded

After redeployment, check the startup logs in Render Dashboard:
```
✅ YouTube cookies loaded successfully
🍪 YouTube cookies: configured
```

If you see `⚠️ Running without cookies`, the environment variable was not set correctly.

### Notes

- **Cookie expiry:** YouTube cookies expire after ~1 year. Refresh them if downloads start failing again.
- **Account safety:** Use a throwaway Google account, not your primary account.
- **Incognito window:** Required to ensure the exported cookies match what yt-dlp uses (no cached sessions).

---

## ✅ Verification Tests

After deployment, run these tests:

### 1. Health Check
```bash
curl https://ytdlp-service.onrender.com/api/v1/health
```
Expected: `{"status":"healthy",...}`

### 2. Info Endpoint
```bash
curl -X POST https://ytdlp-service.onrender.com/api/v1/info \
  -H "Content-Type: application/json" \
  -d '{"video_url":"https://youtube.com/watch?v=dQw4w9WgXcQ"}'
```
Expected: Video metadata returned

### 3. Download Endpoint (optional)
```bash
curl -X POST https://ytdlp-service.onrender.com/api/v1/download \
  -H "Content-Type: application/json" \
  -d '{"video_url":"https://youtube.com/watch?v=jNQXAC9IVRw","job_id":"prod_test","quality":"360p"}'
```
Expected: `{"success":true,"method":"url",...}`

---

## 📊 Monitoring

### Check Deployment Status

1. **Render Dashboard:** https://dashboard.render.com
2. **Service:** ytdlp-service
3. **Tabs to monitor:**
   - **Logs:** Real-time application logs
   - **Metrics:** CPU, Memory, Request rate
   - **Events:** Deployment history

### Key Log Messages

Look for these on successful startup:
```
🚀 Starting yt-dlp download service...
Version: 1.0.0
yt-dlp version: 2026.02.04
Starting cleanup scheduler (interval: 60s)
Application startup complete.
Uvicorn running on http://0.0.0.0:PORT
```

### Health Check

Render automatically monitors `/api/v1/health` endpoint.
- Interval: 30 seconds
- Timeout: 10 seconds
- Failure threshold: 3 consecutive failures

---

## 🐛 Troubleshooting

### Deployment Failed

1. Check build logs in Render Dashboard
2. Verify `requirements.txt` syntax
3. Check Python version compatibility
4. Ensure all imports are available

### Service Not Responding

1. Check application logs
2. Verify environment variables are set
3. Test health endpoint directly
4. Check if service is running (Render dashboard)

### Health Check Failing

1. Verify `/api/v1/health` endpoint exists
2. Check application startup logs
3. Ensure port is correct (`$PORT` environment variable)
4. Check for Python errors in logs

### High Response Time

1. Check instance size (Starter vs Standard)
2. Monitor CPU/Memory usage
3. Check yt-dlp download times
4. Consider upgrading to Standard plan

---

## 🔄 Updating the Service

### Automatic Updates

Service auto-deploys when you push to GitHub main branch:

```bash
cd VideoSyncIntegrations/YTDLPAPI
git add .
git commit -m "Update: description of changes"
git push origin main

# Render automatically deploys in ~5 minutes
```

### Manual Redeploy

1. Go to Render Dashboard
2. Select: ytdlp-service
3. Click: "Manual Deploy" → "Deploy latest commit"
4. Wait for deployment to complete

---

## 💰 Cost Estimate

**Starter Plan:** $7/month
- 512MB RAM
- 0.5 CPU (shared)
- Suitable for 100-500 requests/day

**Standard Plan:** $25/month (if needed)
- 2GB RAM
- 1 CPU (dedicated)
- Better for 1000+ requests/day

**Current expected usage:**
- ~15-25 video downloads per hour
- Well within Starter plan limits

---

## 📝 Production URLs

After deployment, update these locations with production URL:

1. **Backend .env (Render):**
   ```
   YTDLP_API_URL=https://ytdlp-service.onrender.com
   ```

2. **Local .env (for testing):**
   ```
   YTDLP_API_URL=https://ytdlp-service.onrender.com
   ```

3. **Documentation:**
   - Update DEPLOYMENT_GUIDE.md with actual URL
   - Update README.md examples

---

## ✅ Post-Deployment Checklist

After deployment completes:

- [ ] Service is healthy (green status in Render)
- [ ] Health endpoint returns 200
- [ ] Info endpoint works
- [ ] Download endpoint works (test with small video)
- [ ] Backend environment variable updated
- [ ] Backend service restarted
- [ ] Backend logs show "YtdlpApiClient initialized"
- [ ] First test job completes successfully
- [ ] Production URL saved in documentation
- [ ] Monitoring alerts configured (optional)
- [ ] Cost tracking enabled (optional)

---

## 🎯 Success Criteria

**Immediate (Day 1):**
- ✅ Service deployed and healthy
- ✅ All endpoints responding correctly
- ✅ Backend integrated successfully
- ✅ First 5 jobs complete with Strategy #3

**Week 1:**
- ✅ Strategy #3 success rate >70%
- ✅ Pending backlog reduced to <100
- ✅ Zero service outages
- ✅ Average response time <10 seconds

**Month 1:**
- ✅ 95%+ job success rate
- ✅ Cost optimized (80% Apify savings)
- ✅ Zero infinite retry loops
- ✅ All 152 pending jobs processed

---

**Deployment Guide Created:** February 15, 2026
**Ready for Production:** Yes
**Estimated Deployment Time:** 15-20 minutes
