"""
YouTube downloader using multiple strategies with automatic fallback.

Strategy order (tried sequentially until one succeeds):
  1.  yt-dlp ios                  — iOS app protocol, bypasses PO token on datacenter IPs
  2.  yt-dlp ios+cookies          — iOS + authenticated session (if cookies configured)
  3.  yt-dlp android              — Android app protocol, different extraction path
  4.  yt-dlp android+cookies      — Android + authenticated session
  5.  yt-dlp tv_embedded          — TV embedded player (less restricted)
  6.  yt-dlp mweb                 — Mobile web client
  7.  yt-dlp web_creator          — Creator client (different rate limiting)
  8.  cobalt.tools (api.cobalt.tools) — API proxy, bypasses datacenter IP blocking entirely
  9.  cobalt.tools (co.wuk.sh)    — Secondary cobalt instance
  10. invidious (inv.nadeko.net)  — Open-source YouTube frontend; proxies video streams
  11. invidious (yewtu.be)        — Invidious secondary instance
  12. invidious (invidious.nerdvpn.de) — Invidious tertiary instance
  13. pytubefix IOS               — Completely different Python library, IOS client
  14. pytubefix ANDROID           — pytubefix ANDROID client
  15. pytubefix TV_EMBED          — pytubefix TV_EMBED client
  16. streamlink                  — Independent stream extraction library
"""

import asyncio
import base64
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
import yt_dlp

from .models import VideoMetadata, ErrorCode, ErrorDetail
from .storage import storage

logger = logging.getLogger(__name__)

# Optional library availability flags
try:
    import pytubefix  # noqa: F401
    PYTUBEFIX_AVAILABLE = True
    logger.info("✅ pytubefix available (strategies 8-10)")
except ImportError:
    PYTUBEFIX_AVAILABLE = False
    logger.warning("⚠️ pytubefix not installed — strategies 8-10 unavailable. Add pytubefix to requirements.txt")

try:
    import streamlink as _streamlink_check  # noqa: F401
    STREAMLINK_AVAILABLE = True
    logger.info("✅ streamlink available (strategy 11)")
except ImportError:
    STREAMLINK_AVAILABLE = False
    logger.warning("⚠️ streamlink not installed — strategy 11 unavailable. Add streamlink to requirements.txt")

# yt-dlp format selectors by quality
QUALITY_FORMATS = {
    "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}

# Quality label → max pixel height (for pytubefix stream selection)
QUALITY_TO_HEIGHT = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "best": 9999,
}


class YouTubeDownloader:
    """Multi-strategy YouTube downloader with automatic fallback."""

    def __init__(self):
        self.cookies_file: Optional[str] = None
        self.po_token: Optional[str] = os.getenv('YTDLP_PO_TOKEN')
        self.visitor_data: Optional[str] = os.getenv('YTDLP_VISITOR_DATA')
        self._setup_cookies()

    # =========================================================================
    # SETUP HELPERS
    # =========================================================================

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

    def _build_ytdlp_opts(
        self,
        player_clients: List[str],
        use_cookies: bool = False,
        skip_webpage: bool = True,
        output_path: Optional[str] = None,
        format_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a yt-dlp options dict for the given strategy parameters."""
        extractor_args: Dict[str, Any] = {
            'player_client': player_clients,
        }
        if skip_webpage:
            extractor_args['player_skip'] = ['webpage']
        if self.po_token and self.visitor_data:
            extractor_args['po_token'] = [f'web+{self.po_token}']
            extractor_args['visitor_data'] = [self.visitor_data]

        opts: Dict[str, Any] = {
            'user_agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'extractor_args': {'youtube': extractor_args},
            'http_headers': {
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
            },
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'retries': 2,
            'fragment_retries': 2,
            'file_access_retries': 2,
        }

        if use_cookies and self.cookies_file:
            opts['cookiefile'] = self.cookies_file
        if output_path:
            opts['outtmpl'] = output_path
        if format_selector:
            opts['format'] = format_selector

        return opts

    # =========================================================================
    # ERROR CLASSIFICATION
    # =========================================================================

    def _classify_error(self, error_msg: str) -> ErrorDetail:
        """Classify an error string into a structured ErrorDetail."""
        error_lower = error_msg.lower()

        if any(kw in error_lower for kw in ["private", "unavailable", "deleted", "removed", "geo-block"]):
            return ErrorDetail(
                code=ErrorCode.VIDEO_UNAVAILABLE,
                message="Video is private, deleted, or unavailable",
                is_transient=False,
                details={"error": error_msg},
            )

        if any(kw in error_lower for kw in ["sign in", "bot", "confirm you"]):
            return ErrorDetail(
                code=ErrorCode.RATE_LIMITED,
                message="YouTube bot detection triggered — sign-in or cookies required",
                is_transient=True,
                retry_after_seconds=300,
                details={"error": error_msg},
            )

        if "429" in error_lower or "rate limit" in error_lower or "too many requests" in error_lower:
            return ErrorDetail(
                code=ErrorCode.RATE_LIMITED,
                message="Rate limited by YouTube",
                is_transient=True,
                retry_after_seconds=300,
                details={"error": error_msg},
            )

        if "timeout" in error_lower or "timed out" in error_lower:
            return ErrorDetail(
                code=ErrorCode.DOWNLOAD_TIMEOUT,
                message="Download timed out",
                is_transient=True,
                retry_after_seconds=60,
                details={"error": error_msg},
            )

        if any(kw in error_lower for kw in ["network", "connection", "resolve", "unreachable"]):
            return ErrorDetail(
                code=ErrorCode.NETWORK_ERROR,
                message="Network connection error",
                is_transient=True,
                retry_after_seconds=30,
                details={"error": error_msg},
            )

        if "disk" in error_lower or "no space" in error_lower:
            return ErrorDetail(
                code=ErrorCode.DISK_FULL,
                message="Server disk full",
                is_transient=True,
                retry_after_seconds=600,
                details={"error": error_msg},
            )

        if "invalid" in error_lower or "malformed" in error_lower or "unsupported url" in error_lower:
            return ErrorDetail(
                code=ErrorCode.INVALID_URL,
                message="Invalid or unsupported URL",
                is_transient=False,
                details={"error": error_msg},
            )

        return ErrorDetail(
            code=ErrorCode.SERVER_ERROR,
            message="Download failed",
            is_transient=True,
            retry_after_seconds=120,
            details={"error": error_msg},
        )

    def _is_permanent_error(self, error: ErrorDetail) -> bool:
        """True if retrying with a different strategy cannot fix this error."""
        return error.code in (ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.INVALID_URL)

    # =========================================================================
    # METADATA EXTRACTION
    # =========================================================================

    def _extract_metadata_from_ytdlp(self, info: Dict[str, Any]) -> VideoMetadata:
        """Build VideoMetadata from a yt-dlp info dict."""
        return VideoMetadata(
            title=info.get("title", "Unknown"),
            duration_seconds=float(info.get("duration") or 0),
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
            is_private=False,
        )

    # =========================================================================
    # INDIVIDUAL STRATEGY IMPLEMENTATIONS
    # =========================================================================

    async def _run_ytdlp_strategy(
        self,
        video_url: str,
        output_path: Path,
        format_selector: str,
        player_clients: List[str],
        use_cookies: bool,
        skip_webpage: bool,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Run a single yt-dlp download with the given options."""
        opts = self._build_ytdlp_opts(
            player_clients=player_clients,
            use_cookies=use_cookies,
            skip_webpage=skip_webpage,
            output_path=str(output_path),
            format_selector=format_selector,
        )

        def _do_download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(video_url, download=True)

        loop = asyncio.get_event_loop()
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=300,
            )
        except asyncio.TimeoutError:
            return None, None, "yt-dlp strategy timed out after 5 minutes"
        except yt_dlp.utils.DownloadError as e:
            return None, None, str(e)
        except Exception as e:
            return None, None, str(e)

        if not info:
            return None, None, "yt-dlp returned no info"

        # yt-dlp may save with a slightly different name; find the actual file
        actual_path = output_path
        if not actual_path.exists():
            candidates = sorted(output_path.parent.glob("video.*"), key=lambda p: p.stat().st_size, reverse=True)
            if candidates:
                actual_path = candidates[0]
            else:
                return None, None, "File not found on disk after yt-dlp download"

        metadata = self._extract_metadata_from_ytdlp(info)
        metadata.file_size_bytes = actual_path.stat().st_size
        return actual_path, metadata, None

    async def _run_pytubefix_strategy(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
        client_name: str,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Run a pytubefix download with the specified client."""
        if not PYTUBEFIX_AVAILABLE:
            return None, None, "pytubefix not installed"

        max_height = QUALITY_TO_HEIGHT.get(quality, 720)

        def _do_download():
            from pytubefix import YouTube

            yt = YouTube(video_url, client=client_name)

            # Prefer progressive mp4 streams (video+audio in one file)
            progressive_streams = yt.streams.filter(
                progressive=True, file_extension='mp4'
            )

            # Pick the highest-resolution stream at or below the requested quality
            stream = None
            best_height = 0
            for s in progressive_streams:
                if s.resolution:
                    try:
                        h = int(s.resolution.rstrip('p'))
                        if h <= max_height and h > best_height:
                            best_height = h
                            stream = s
                    except ValueError:
                        pass

            # If nothing within quality limit, just take the best available
            if stream is None:
                stream = progressive_streams.get_highest_resolution()

            if stream is None:
                raise Exception("No MP4 progressive stream available via pytubefix")

            downloaded = stream.download(
                output_path=str(job_dir),
                filename="video.mp4",
            )

            meta = {
                'title': yt.title or 'Unknown',
                'duration': yt.length or 0,
                'video_id': yt.video_id,
                'channel_id': getattr(yt, 'channel_id', None),
                'channel_name': yt.author,
                'view_count': yt.views,
            }
            return downloaded, meta

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=300,
            )
        except asyncio.TimeoutError:
            return None, None, "pytubefix strategy timed out after 5 minutes"
        except Exception as e:
            return None, None, str(e)

        if not result:
            return None, None, "pytubefix returned no result"

        downloaded_file, meta = result
        if not downloaded_file or not Path(downloaded_file).exists():
            return None, None, "pytubefix: file not found after download"

        actual_path = Path(downloaded_file)
        metadata = VideoMetadata(
            title=meta.get('title', 'Unknown'),
            duration_seconds=float(meta.get('duration') or 0),
            file_size_bytes=actual_path.stat().st_size,
            format='mp4',
            video_id=meta.get('video_id'),
            channel_id=meta.get('channel_id'),
            channel_name=meta.get('channel_name'),
            view_count=meta.get('view_count'),
            is_live=False,
            is_private=False,
        )
        return actual_path, metadata, None

    async def _run_cobalt_strategy(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
        api_url: str,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Download via cobalt.tools API — proxies through cobalt servers, bypassing datacenter IP blocking."""
        cobalt_quality = {
            "360p": "360", "480p": "480", "720p": "720", "1080p": "1080", "best": "max"
        }.get(quality, "720")
        output_path = job_dir / "video.mp4"

        def _do_download():
            import httpx

            # Step 1: request a stream URL from cobalt API
            try:
                resp = httpx.post(
                    api_url,
                    json={"url": video_url, "videoQuality": cobalt_quality, "downloadMode": "auto"},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=30,
                    follow_redirects=True,
                )
            except Exception as e:
                return None, None, f"cobalt API request failed: {e}"

            if resp.status_code != 200:
                return None, None, f"cobalt API HTTP {resp.status_code}: {resp.text[:200]}"

            try:
                data = resp.json()
            except Exception:
                return None, None, f"cobalt invalid JSON: {resp.text[:200]}"

            status = data.get("status", "")

            if status == "error":
                err = data.get("error", {})
                code = err.get("code", str(err)) if isinstance(err, dict) else str(err)
                return None, None, f"cobalt error: {code}"

            if status not in ("stream", "redirect", "tunnel", "picker"):
                return None, None, f"cobalt unexpected status '{status}': {str(data)[:200]}"

            # For "picker" (multiple files) use the first item's URL
            if status == "picker":
                items = data.get("picker", [])
                if not items:
                    return None, None, "cobalt picker returned no items"
                stream_url = items[0].get("url")
            else:
                stream_url = data.get("url")

            if not stream_url:
                return None, None, "cobalt returned no stream URL"

            # Step 2: download the proxied file
            try:
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream("GET", stream_url) as resp2:
                        if resp2.status_code not in (200, 206):
                            return None, None, f"cobalt stream HTTP {resp2.status_code}"
                        with open(output_path, "wb") as f:
                            for chunk in resp2.iter_bytes(65536):
                                f.write(chunk)
            except Exception as e:
                return None, None, f"cobalt download failed: {e}"

            if not output_path.exists() or output_path.stat().st_size == 0:
                return None, None, "cobalt: empty or missing file after download"

            metadata = VideoMetadata(
                title="Unknown",
                duration_seconds=0.0,
                file_size_bytes=output_path.stat().st_size,
                format="mp4",
                is_live=False,
                is_private=False,
            )
            return output_path, metadata, None

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=360,
            )
        except asyncio.TimeoutError:
            return None, None, "cobalt strategy timed out after 6 minutes"
        except Exception as e:
            return None, None, str(e)

        return result

    async def _run_invidious_strategy(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
        instance: str,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Download via Invidious instance — proxied through their servers, bypasses YouTube CDN IP-locking."""
        import re

        vid_match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
        if not vid_match:
            return None, None, f"cannot extract video ID from URL: {video_url}"
        video_id = vid_match.group(1)
        max_height = QUALITY_TO_HEIGHT.get(quality, 720)
        output_path = job_dir / "video.mp4"

        def _do_download():
            import httpx

            # Step 1: fetch video metadata; local=true makes Invidious proxy URLs through its own servers
            try:
                resp = httpx.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    params={"local": "true"},
                    timeout=30,
                )
            except Exception as e:
                return None, None, f"Invidious API request failed: {e}"

            if resp.status_code != 200:
                return None, None, f"Invidious API HTTP {resp.status_code}"

            try:
                data = resp.json()
            except Exception:
                return None, None, "Invidious invalid JSON response"

            if "error" in data:
                return None, None, f"Invidious error: {data['error']}"

            # Step 2: pick the best format stream
            format_streams = data.get("formatStreams", [])
            if not format_streams:
                return None, None, "Invidious: no format streams available"

            best_stream = None
            best_height = 0
            for stream in format_streams:
                res = stream.get("resolution", "0x0")
                try:
                    h = int(res.split("x")[1]) if "x" in res else int(res.rstrip("p"))
                    if h <= max_height and h > best_height:
                        best_height = h
                        best_stream = stream
                except (ValueError, IndexError):
                    pass

            if best_stream is None:
                best_stream = format_streams[-1]  # fallback to last available

            stream_url = best_stream.get("url")
            if not stream_url:
                return None, None, "Invidious: stream has no URL"

            # Step 3: download (URL is proxied through Invidious servers when local=true)
            try:
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream("GET", stream_url) as resp2:
                        if resp2.status_code not in (200, 206):
                            return None, None, f"Invidious download HTTP {resp2.status_code}"
                        with open(output_path, "wb") as f:
                            for chunk in resp2.iter_bytes(65536):
                                f.write(chunk)
            except Exception as e:
                return None, None, f"Invidious download failed: {e}"

            if not output_path.exists() or output_path.stat().st_size == 0:
                return None, None, "Invidious: empty or missing file after download"

            metadata = VideoMetadata(
                title=data.get("title", "Unknown"),
                duration_seconds=float(data.get("lengthSeconds") or 0),
                file_size_bytes=output_path.stat().st_size,
                format="mp4",
                video_id=video_id,
                view_count=int(data["viewCount"]) if data.get("viewCount") else None,
                is_live=False,
                is_private=False,
            )
            return output_path, metadata, None

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=360,
            )
        except asyncio.TimeoutError:
            return None, None, "Invidious strategy timed out after 6 minutes"
        except Exception as e:
            return None, None, str(e)

        return result

    async def _run_streamlink_strategy(
        self,
        video_url: str,
        job_dir: Path,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Run a streamlink-based download (works best for live streams and HLS VODs)."""
        if not STREAMLINK_AVAILABLE:
            return None, None, "streamlink not installed"

        output_path = job_dir / "video.ts"

        def _do_download():
            from streamlink import Streamlink

            sl = Streamlink()
            streams = sl.streams(video_url)

            if not streams:
                raise Exception("streamlink found no streams for this URL")

            # Quality preference order
            stream = None
            for quality_key in ("best", "720p", "480p", "360p", "worst"):
                if quality_key in streams:
                    stream = streams[quality_key]
                    break
            if stream is None:
                stream = next(iter(streams.values()))

            fd = stream.open()
            try:
                with open(str(output_path), 'wb') as f:
                    while True:
                        chunk = fd.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            finally:
                fd.close()

        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=300,
            )
        except asyncio.TimeoutError:
            return None, None, "streamlink strategy timed out after 5 minutes"
        except Exception as e:
            return None, None, str(e)

        if not output_path.exists() or output_path.stat().st_size == 0:
            return None, None, "streamlink produced an empty or missing file"

        metadata = VideoMetadata(
            title="Unknown",
            duration_seconds=0.0,
            file_size_bytes=output_path.stat().st_size,
            format='ts',
            is_live=False,
            is_private=False,
        )
        return output_path, metadata, None

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    async def get_info(self, video_url: str) -> tuple[Optional[VideoMetadata], Optional[ErrorDetail]]:
        """Get video metadata without downloading."""
        opts = self._build_ytdlp_opts(
            player_clients=['ios', 'tv_embedded', 'mweb'],
            use_cookies=bool(self.cookies_file),
            skip_webpage=not bool(self.cookies_file),
        )
        opts['skip_download'] = True

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(video_url, download=False)

        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, _extract)
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt-dlp info extraction failed: {e}")
            return None, self._classify_error(str(e))
        except Exception as e:
            logger.error(f"Unexpected error during info extraction: {e}")
            return None, ErrorDetail(
                code=ErrorCode.SERVER_ERROR,
                message=f"Unexpected error: {str(e)}",
                is_transient=True,
                retry_after_seconds=120,
            )

        if not info:
            return None, ErrorDetail(
                code=ErrorCode.VIDEO_UNAVAILABLE,
                message="Could not extract video info",
                is_transient=False,
            )

        return self._extract_metadata_from_ytdlp(info), None

    async def download(
        self,
        video_url: str,
        job_id: str,
        quality: str = "720p",
        output_format: str = "mp4",
        timeout_seconds: int = 3600,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[ErrorDetail]]:
        """
        Download a YouTube video using up to 16 strategies with automatic fallback.

        Returns (file_path, metadata, None) on success.
        Returns (None, None, error) if all strategies fail.
        """
        job_dir = storage.get_job_dir(job_id)
        output_path = job_dir / f"video.{output_format}"
        format_selector = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["720p"])

        has_cookies = bool(self.cookies_file)

        # Build the ordered strategy list.
        # Each entry: (display_name, kind, kwargs_dict)
        # kind = "ytdlp" | "pytubefix" | "streamlink"
        strategies = []

        # --- yt-dlp strategies ---
        # Strategy 1: ios client without cookies — best for datacenter IPs (bypasses PO token)
        strategies.append(("yt-dlp ios", "ytdlp", {
            "player_clients": ["ios"], "use_cookies": False, "skip_webpage": True
        }))

        # Strategy 2: ios + cookies — authenticated session reduces bot detection
        if has_cookies:
            strategies.append(("yt-dlp ios+cookies", "ytdlp", {
                "player_clients": ["ios"], "use_cookies": True, "skip_webpage": False
            }))

        # Strategy 3: android client — different extraction path, often less blocked
        strategies.append(("yt-dlp android", "ytdlp", {
            "player_clients": ["android"], "use_cookies": False, "skip_webpage": True
        }))

        # Strategy 4: android + cookies
        if has_cookies:
            strategies.append(("yt-dlp android+cookies", "ytdlp", {
                "player_clients": ["android"], "use_cookies": True, "skip_webpage": False
            }))

        # Strategy 5: tv_embedded — TV embedded player client
        strategies.append(("yt-dlp tv_embedded", "ytdlp", {
            "player_clients": ["tv_embedded"],
            "use_cookies": has_cookies,
            "skip_webpage": not has_cookies,
        }))

        # Strategy 6: mweb — mobile web
        strategies.append(("yt-dlp mweb", "ytdlp", {
            "player_clients": ["mweb"], "use_cookies": False, "skip_webpage": True
        }))

        # Strategy 7: web_creator — creator-specific client with different rate limits
        strategies.append(("yt-dlp web_creator", "ytdlp", {
            "player_clients": ["web_creator"],
            "use_cookies": has_cookies,
            "skip_webpage": not has_cookies,
        }))

        # --- API-based proxy strategies (bypass datacenter IP blocking entirely) ---
        # cobalt.tools — downloads YouTube via its own proxy servers, no direct YouTube IP needed
        strategies.append(("cobalt.tools (api.cobalt.tools)", "cobalt", {
            "api_url": "https://api.cobalt.tools/",
        }))
        # Secondary cobalt instance (community-hosted)
        strategies.append(("cobalt.tools (co.wuk.sh)", "cobalt", {
            "api_url": "https://co.wuk.sh/api/json",
        }))

        # Invidious — open-source YouTube frontend that proxies video streams through its own servers
        strategies.append(("invidious (inv.nadeko.net)", "invidious", {
            "instance": "https://inv.nadeko.net",
        }))
        strategies.append(("invidious (yewtu.be)", "invidious", {
            "instance": "https://yewtu.be",
        }))
        strategies.append(("invidious (invidious.nerdvpn.de)", "invidious", {
            "instance": "https://invidious.nerdvpn.de",
        }))

        # --- pytubefix strategies (completely different Python library) ---
        if PYTUBEFIX_AVAILABLE:
            strategies.append(("pytubefix IOS", "pytubefix", {"client_name": "IOS"}))
            strategies.append(("pytubefix ANDROID", "pytubefix", {"client_name": "ANDROID"}))
            strategies.append(("pytubefix TV_EMBED", "pytubefix", {"client_name": "TV_EMBED"}))

        # --- streamlink strategy (independent stream extractor) ---
        if STREAMLINK_AVAILABLE:
            strategies.append(("streamlink", "streamlink", {}))

        total = len(strategies)
        logger.info(f"🚀 Starting download with {total} strategies: {video_url}")

        last_error: Optional[ErrorDetail] = None
        all_errors: List[str] = []

        for idx, (name, kind, kwargs) in enumerate(strategies, 1):
            logger.info(f"🎯 Strategy {idx}/{total}: {name}")

            # Clean up any partial files from previous attempt
            for leftover in job_dir.glob("video.*"):
                try:
                    leftover.unlink()
                except Exception:
                    pass

            file_path: Optional[Path] = None
            metadata: Optional[VideoMetadata] = None
            error_msg: Optional[str] = None

            try:
                if kind == "ytdlp":
                    file_path, metadata, error_msg = await self._run_ytdlp_strategy(
                        video_url, output_path, format_selector,
                        player_clients=kwargs["player_clients"],
                        use_cookies=kwargs["use_cookies"],
                        skip_webpage=kwargs["skip_webpage"],
                    )
                elif kind == "cobalt":
                    file_path, metadata, error_msg = await self._run_cobalt_strategy(
                        video_url, job_dir, quality,
                        api_url=kwargs["api_url"],
                    )
                elif kind == "invidious":
                    file_path, metadata, error_msg = await self._run_invidious_strategy(
                        video_url, job_dir, quality,
                        instance=kwargs["instance"],
                    )
                elif kind == "pytubefix":
                    file_path, metadata, error_msg = await self._run_pytubefix_strategy(
                        video_url, job_dir, quality,
                        client_name=kwargs["client_name"],
                    )
                elif kind == "streamlink":
                    file_path, metadata, error_msg = await self._run_streamlink_strategy(
                        video_url, job_dir,
                    )
            except Exception as e:
                error_msg = f"Unexpected exception in strategy: {e}"

            # Check for success
            if file_path and file_path.exists() and file_path.stat().st_size > 0:
                size_mb = file_path.stat().st_size / 1024 / 1024
                logger.info(f"✅ Strategy {idx}/{total} ({name}) succeeded! {file_path.name} ({size_mb:.1f} MB)")
                return file_path, metadata, None

            # Strategy failed
            error_summary = error_msg or "unknown error"
            logger.warning(f"⚠️ Strategy {idx}/{total} ({name}) failed: {error_summary[:120]}")
            all_errors.append(f"[{name}]: {error_summary[:200]}")

            if error_msg:
                classified = self._classify_error(error_msg)
                last_error = classified
                if self._is_permanent_error(classified):
                    logger.error(f"❌ Permanent error — stopping all strategies: {error_summary[:120]}")
                    break

        # All strategies exhausted
        logger.error(f"❌ All {total} strategies failed for {video_url}")

        if last_error:
            last_error.details = {"all_strategy_errors": all_errors}
            return None, None, last_error

        return None, None, ErrorDetail(
            code=ErrorCode.SERVER_ERROR,
            message=f"All {total} download strategies failed",
            is_transient=True,
            retry_after_seconds=300,
            details={"all_strategy_errors": all_errors},
        )


# Global singleton
downloader = YouTubeDownloader()
