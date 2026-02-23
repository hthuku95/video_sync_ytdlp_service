"""
FastAPI yt-dlp Download Service
High-performance YouTube download microservice for video_editor backend
"""

import os
import time
import base64
import logging
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

from .models import (
    DownloadRequest,
    DownloadResponse,
    ErrorResponse,
    InfoRequest,
    InfoResponse,
    HealthResponse,
    HealthStats,
    VideoMetadata,
    ErrorCode,
    ErrorDetail,
)
from .downloader import downloader
from .proxy_manager import proxy_manager
from .storage import storage

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# App metadata
VERSION = "1.0.0"
start_time = time.time()

# Statistics tracking
stats = {
    "total_downloads": 0,
    "active_downloads": 0,
    "failed_downloads": 0,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup/shutdown tasks"""
    import asyncio as _asyncio

    # Startup
    logger.info("🚀 Starting yt-dlp download service...")
    logger.info(f"Version: {VERSION}")
    logger.info(f"yt-dlp version: {yt_dlp.version.__version__}")

    cookies_configured = bool(os.getenv('YTDLP_COOKIES_B64'))
    po_token_configured = bool(os.getenv('YTDLP_PO_TOKEN'))
    logger.info(f"🍪 YouTube cookies: {'configured' if cookies_configured else 'NOT configured (bot detection risk)'}")
    logger.info(f"🎫 PO token: {'configured' if po_token_configured else 'not set'}")

    # Fetch residential proxies from Webshare on startup
    await proxy_manager.refresh()
    # Start hourly background refresh
    _asyncio.create_task(proxy_manager.auto_refresh_loop())

    # Start cleanup scheduler
    await storage.start_cleanup_scheduler()

    yield

    # Shutdown
    logger.info("Shutting down yt-dlp download service...")
    await storage.stop_cleanup_scheduler()


# Create FastAPI app
app = FastAPI(
    title="YT-DLP Download Service",
    description="High-performance YouTube download microservice using yt-dlp",
    version=VERSION,
    lifespan=lifespan,
)

# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.post("/api/v1/download", response_model=DownloadResponse)
async def download_video(
    request: DownloadRequest,
    background_tasks: BackgroundTasks
) -> Response:
    """
    Download YouTube video and return file URL or base64 data

    **Flow:**
    1. Download video using yt-dlp
    2. If file < 50MB and prefer_base64=True, return base64-encoded data
    3. Otherwise, return temporary download URL (5-minute expiration)
    4. Schedule file cleanup after TTL
    """
    job_id = request.job_id or f"job_{int(time.time() * 1000)}"

    logger.info(f"📥 Download request: {request.video_url} (job_id={job_id}, quality={request.quality})")

    # Update stats
    stats["active_downloads"] += 1

    try:
        # Download video
        file_path, metadata, error = await downloader.download(
            video_url=request.video_url,
            job_id=job_id,
            quality=request.quality,
            output_format=request.format,
            timeout_seconds=request.timeout_seconds,
            only_strategy=request.only_strategy,
        )

        if error or not file_path:
            stats["failed_downloads"] += 1
            logger.error(f"❌ Download failed: {error.message if error else 'Unknown error'}")
            return JSONResponse(
                status_code=500 if error.is_transient else 400,
                content=ErrorResponse(error=error).model_dump()
            )

        stats["total_downloads"] += 1

        # Determine response method
        file_size = file_path.stat().st_size
        max_base64_size = 50 * 1024 * 1024  # 50 MB

        if request.prefer_base64 and file_size <= max_base64_size:
            # Return base64-encoded file
            logger.info(f"✅ Encoding file as base64 ({file_size / 1024 / 1024:.2f} MB)")

            with open(file_path, "rb") as f:
                file_data = base64.b64encode(f.read()).decode("utf-8")

            # Schedule cleanup
            background_tasks.add_task(storage.delete_job_files, job_id)

            return JSONResponse(
                content=DownloadResponse(
                    success=True,
                    method="base64",
                    file_data=file_data,
                    metadata=metadata,
                ).model_dump(mode='json')
            )
        else:
            # Return download URL
            download_url = f"/downloads/{job_id}/{file_path.name}"
            expires_at = datetime.utcnow() + timedelta(seconds=storage.file_ttl)

            logger.info(f"✅ Download URL: {download_url} (expires: {expires_at.isoformat()})")

            return JSONResponse(
                content=DownloadResponse(
                    success=True,
                    method="url",
                    download_url=download_url,
                    expires_at=expires_at,
                    metadata=metadata,
                ).model_dump(mode='json')
            )

    except Exception as e:
        stats["failed_downloads"] += 1
        logger.exception(f"💥 Unexpected error during download: {e}")
        error = ErrorDetail(
            code=ErrorCode.SERVER_ERROR,
            message=f"Internal server error: {str(e)}",
            is_transient=True,
            retry_after_seconds=120
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=error).model_dump()
        )
    finally:
        stats["active_downloads"] -= 1


@app.get("/downloads/{job_id}/{filename}")
async def serve_download(job_id: str, filename: str, background_tasks: BackgroundTasks):
    """
    Serve downloaded file (temporary URL with auto-cleanup)
    """
    file_path = storage.get_download_path(job_id, filename)

    if not file_path.exists():
        logger.warning(f"⚠️ File not found or expired: {job_id}/{filename}")
        raise HTTPException(status_code=404, detail="File not found or expired")

    # Check file age
    age = storage.get_file_age(job_id, filename)
    if age and age > storage.file_ttl:
        logger.warning(f"⚠️ File expired ({age:.0f}s > {storage.file_ttl}s): {job_id}/{filename}")
        background_tasks.add_task(storage.delete_job_files, job_id)
        raise HTTPException(status_code=410, detail="File expired")

    logger.info(f"📤 Serving file: {job_id}/{filename} ({file_path.stat().st_size / 1024 / 1024:.2f} MB)")

    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=filename,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.post("/api/v1/info", response_model=InfoResponse)
async def get_video_info(request: InfoRequest) -> Response:
    """
    Get video metadata without downloading

    **Use case:** Check video availability and metadata before downloading
    """
    logger.info(f"ℹ️ Info request: {request.video_url}")

    metadata, error = await downloader.get_info(request.video_url)

    if error or not metadata:
        logger.error(f"❌ Info extraction failed: {error.message if error else 'Unknown error'}")
        return JSONResponse(
            status_code=500 if error.is_transient else 400,
            content=ErrorResponse(error=error).model_dump()
        )

    logger.info(f"✅ Info extracted: {metadata.title} ({metadata.duration_seconds}s)")

    return JSONResponse(
        content=InfoResponse(
            success=True,
            metadata=metadata
        ).model_dump(mode='json')
    )


@app.get("/api/v1/strategies")
async def list_strategies():
    """List all available download strategies with their 1-based index numbers."""
    strategies = downloader._build_strategy_list()
    return {
        "total": len(strategies),
        "strategies": [
            {"num": i + 1, "name": name, "kind": kind}
            for i, (name, kind, _) in enumerate(strategies)
        ]
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint for monitoring

    **Metrics:**
    - Service status and uptime
    - Download statistics
    - Disk usage
    - yt-dlp version
    """
    uptime = time.time() - start_time
    disk_usage = storage.get_disk_usage()

    return HealthResponse(
        status="healthy",
        version=VERSION,
        uptime_seconds=uptime,
        stats=HealthStats(
            total_downloads=stats["total_downloads"],
            active_downloads=stats["active_downloads"],
            failed_downloads=stats["failed_downloads"],
            disk_usage_percent=disk_usage,
        ),
        yt_dlp_version=yt_dlp.version.__version__,
    )


@app.get("/")
async def root():
    """Root endpoint with service info"""
    return {
        "service": "yt-dlp Download Service",
        "version": VERSION,
        "status": "running",
        "endpoints": {
            "download": "/api/v1/download",
            "info": "/api/v1/info",
            "health": "/api/v1/health",
        },
        "docs": "/docs",
    }


# ============================================================================
# ERROR HANDLERS
# ============================================================================


@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Custom 404 handler"""
    return JSONResponse(
        status_code=404,
        content={"detail": "Endpoint not found. See /docs for API documentation."}
    )


@app.exception_handler(500)
async def server_error_handler(request, exc):
    """Custom 500 handler"""
    logger.exception("Internal server error")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error. Please try again later.",
            "is_transient": True,
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
