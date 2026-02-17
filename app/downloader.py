"""
YouTube downloader using yt-dlp with anti-bot measures
"""

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional
import logging
import yt_dlp

from .models import VideoMetadata, ErrorCode, ErrorDetail
from .storage import storage

logger = logging.getLogger(__name__)

# Quality mapping to yt-dlp format selectors
QUALITY_FORMATS = {
    "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}


class YouTubeDownloader:
    """Wrapper around yt-dlp with anti-bot measures"""

    def __init__(self):
        self.cookies_file: Optional[str] = None
        self.po_token: Optional[str] = os.getenv('YTDLP_PO_TOKEN')
        self.visitor_data: Optional[str] = os.getenv('YTDLP_VISITOR_DATA')

        self._setup_cookies()

        # Build extractor args.
        # In 2026, YouTube requires PO tokens for datacenter IPs with web/tv_embedded.
        # 'ios' uses the YouTube iOS app protocol which bypasses this requirement.
        # 'mediaconnect' is another low-restriction client.
        # We try ios first, fall back to tv_embedded (works with valid cookies+PO token),
        # then mweb as last resort.
        extractor_args: Dict[str, Any] = {
            'player_client': ['ios', 'tv_embedded', 'mweb'],
        }
        # Only skip webpage when no cookies — avoids 429 on initial page load.
        # When cookies are present, webpage loading is fine (authenticated session).
        if not self.cookies_file:
            extractor_args['player_skip'] = ['webpage']

        if self.po_token and self.visitor_data:
            extractor_args['po_token'] = [f'web+{self.po_token}']
            extractor_args['visitor_data'] = [self.visitor_data]

        self.base_opts: Dict[str, Any] = {
            # Anti-bot measures
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'extractor_args': {'youtube': extractor_args},
            'http_headers': {
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
            },

            # Download settings
            'merge_output_format': 'mp4',
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,

            # Retries
            'retries': 3,
            'fragment_retries': 3,
            'file_access_retries': 3,
        }

        # Attach cookies file if available
        if self.cookies_file:
            self.base_opts['cookiefile'] = self.cookies_file

    def _setup_cookies(self) -> None:
        """Load YouTube cookies from YTDLP_COOKIES_B64 environment variable."""
        cookies_b64 = os.getenv('YTDLP_COOKIES_B64', '').strip()
        if not cookies_b64:
            logger.warning(
                "⚠️ Running without cookies - downloads may fail due to bot detection. "
                "Set YTDLP_COOKIES_B64 to enable cookie authentication."
            )
            return

        try:
            cookies_bytes = base64.b64decode(cookies_b64)
            cookies_path = '/tmp/ytdlp_cookies.txt'
            with open(cookies_path, 'wb') as f:
                f.write(cookies_bytes)
            self.cookies_file = cookies_path
            logger.info("✅ YouTube cookies loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load YouTube cookies: {e}")

    def _get_format_selector(self, quality: str, output_format: str) -> str:
        """Get yt-dlp format selector based on quality and format"""
        base_format = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["720p"])

        if output_format == "webm":
            base_format = base_format.replace("mp4", "webm").replace("m4a", "webm")
        elif output_format == "mkv":
            base_format = base_format.replace("mp4", "mkv").replace("m4a", "mkv")

        return base_format

    def _classify_error(self, error_msg: str) -> ErrorDetail:
        """Classify yt-dlp error into error codes"""
        error_lower = error_msg.lower()

        # Private/unavailable videos
        if any(keyword in error_lower for keyword in ["private", "unavailable", "deleted", "removed", "geo-block"]):
            return ErrorDetail(
                code=ErrorCode.VIDEO_UNAVAILABLE,
                message="Video is private, deleted, or unavailable",
                is_transient=False,
                details={"yt_dlp_error": error_msg}
            )

        # Bot detection / sign-in required
        if any(keyword in error_lower for keyword in ["sign in", "bot", "confirm you"]):
            return ErrorDetail(
                code=ErrorCode.RATE_LIMITED,
                message="YouTube bot detection triggered - sign-in or cookies required",
                is_transient=True,
                retry_after_seconds=300,
                details={"yt_dlp_error": error_msg}
            )

        # Rate limiting
        if "429" in error_lower or "rate limit" in error_lower or "too many requests" in error_lower:
            return ErrorDetail(
                code=ErrorCode.RATE_LIMITED,
                message="Rate limited by YouTube",
                is_transient=True,
                retry_after_seconds=300,  # 5 minutes
                details={"yt_dlp_error": error_msg}
            )

        # Timeout errors
        if "timeout" in error_lower or "timed out" in error_lower:
            return ErrorDetail(
                code=ErrorCode.DOWNLOAD_TIMEOUT,
                message="Download timed out",
                is_transient=True,
                retry_after_seconds=60,
                details={"yt_dlp_error": error_msg}
            )

        # Network errors
        if any(keyword in error_lower for keyword in ["network", "connection", "resolve", "unreachable"]):
            return ErrorDetail(
                code=ErrorCode.NETWORK_ERROR,
                message="Network connection error",
                is_transient=True,
                retry_after_seconds=30,
                details={"yt_dlp_error": error_msg}
            )

        # Disk errors
        if "disk" in error_lower or "space" in error_lower or "no space" in error_lower:
            return ErrorDetail(
                code=ErrorCode.DISK_FULL,
                message="Server disk full",
                is_transient=True,
                retry_after_seconds=600,  # 10 minutes
                details={"yt_dlp_error": error_msg}
            )

        # Invalid URL
        if "invalid" in error_lower or "malformed" in error_lower or "unsupported" in error_lower:
            return ErrorDetail(
                code=ErrorCode.INVALID_URL,
                message="Invalid or unsupported URL",
                is_transient=False,
                details={"yt_dlp_error": error_msg}
            )

        # Generic server error
        return ErrorDetail(
            code=ErrorCode.SERVER_ERROR,
            message="Download failed",
            is_transient=True,
            retry_after_seconds=120,
            details={"yt_dlp_error": error_msg}
        )

    def _extract_metadata(self, info: Dict[str, Any]) -> VideoMetadata:
        """Extract metadata from yt-dlp info dict"""
        return VideoMetadata(
            title=info.get("title", "Unknown"),
            duration_seconds=info.get("duration", 0.0),
            width=info.get("width"),
            height=info.get("height"),
            file_size_bytes=info.get("filesize") or info.get("filesize_approx"),
            format=info.get("ext", "mp4"),
            video_id=info.get("id"),
            channel_id=info.get("channel_id"),
            channel_name=info.get("channel") or info.get("uploader"),
            upload_date=info.get("upload_date"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
            is_live=info.get("is_live", False),
            is_private=False  # If we got metadata, it's not private
        )

    async def get_info(self, video_url: str) -> tuple[Optional[VideoMetadata], Optional[ErrorDetail]]:
        """Get video metadata without downloading"""
        try:
            opts = self.base_opts.copy()
            opts['skip_download'] = True

            def _extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(video_url, download=False)

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, _extract)

            if not info:
                return None, ErrorDetail(
                    code=ErrorCode.VIDEO_UNAVAILABLE,
                    message="Could not extract video info",
                    is_transient=False
                )

            metadata = self._extract_metadata(info)
            return metadata, None

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt-dlp info extraction failed: {e}")
            error = self._classify_error(str(e))
            return None, error
        except Exception as e:
            logger.error(f"Unexpected error during info extraction: {e}")
            return None, ErrorDetail(
                code=ErrorCode.SERVER_ERROR,
                message=f"Unexpected error: {str(e)}",
                is_transient=True,
                retry_after_seconds=120
            )

    async def download(
        self,
        video_url: str,
        job_id: str,
        quality: str = "720p",
        output_format: str = "mp4",
        timeout_seconds: int = 3600
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[ErrorDetail]]:
        """
        Download video and return (file_path, metadata, error)
        Returns (Path, metadata, None) on success, (None, None, error) on failure
        """
        job_dir = storage.get_job_dir(job_id)
        output_path = job_dir / f"video.{output_format}"

        try:
            opts = self.base_opts.copy()
            opts.update({
                'format': self._get_format_selector(quality, output_format),
                'outtmpl': str(output_path),
                'socket_timeout': min(timeout_seconds, 300),  # Max 5 min socket timeout
            })

            def _download():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    return info

            # Run download in thread pool with timeout
            loop = asyncio.get_event_loop()
            try:
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, _download),
                    timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                logger.error(f"Download timeout after {timeout_seconds}s: {video_url}")
                return None, None, ErrorDetail(
                    code=ErrorCode.DOWNLOAD_TIMEOUT,
                    message=f"Download exceeded {timeout_seconds}s timeout",
                    is_transient=True,
                    retry_after_seconds=60
                )

            if not info:
                return None, None, ErrorDetail(
                    code=ErrorCode.SERVER_ERROR,
                    message="Download completed but no info returned",
                    is_transient=True
                )

            # Verify file exists
            if not output_path.exists():
                # yt-dlp might have saved with different extension
                actual_files = list(job_dir.glob("video.*"))
                if actual_files:
                    output_path = actual_files[0]
                    logger.info(f"File saved as {output_path.name} instead of video.{output_format}")
                else:
                    return None, None, ErrorDetail(
                        code=ErrorCode.SERVER_ERROR,
                        message="Download reported success but file not found",
                        is_transient=True
                    )

            # Extract metadata
            metadata = self._extract_metadata(info)

            # Update file size from actual file
            metadata.file_size_bytes = output_path.stat().st_size

            logger.info(f"Download successful: {video_url} → {output_path} ({metadata.file_size_bytes / 1024 / 1024:.2f} MB)")
            return output_path, metadata, None

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt-dlp download failed: {e}")
            error = self._classify_error(str(e))
            return None, None, error
        except Exception as e:
            logger.error(f"Unexpected download error: {e}")
            return None, None, ErrorDetail(
                code=ErrorCode.SERVER_ERROR,
                message=f"Unexpected error: {str(e)}",
                is_transient=True,
                retry_after_seconds=120
            )


# Global downloader instance
downloader = YouTubeDownloader()
