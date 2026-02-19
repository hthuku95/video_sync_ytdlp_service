"""
YouTube downloader using multiple strategies with automatic fallback.

Strategy order (tried sequentially until one succeeds):
  When YTDLP_PROXY is set, proxy-first strategies are tried first:
  P1. yt-dlp ios+proxy            — iOS client routed through residential proxy
  P2. yt-dlp android+proxy        — Android client through residential proxy
  P3. yt-dlp web+proxy            — Web client through residential proxy

  Standard strategies (all also use proxy if YTDLP_PROXY is set):
  1.  yt-dlp ios                  — iOS app protocol, bypasses PO token on datacenter IPs
  2.  yt-dlp ios+cookies          — iOS + authenticated session (if cookies configured)
  3.  yt-dlp android              — Android app protocol, different extraction path
  4.  yt-dlp android+cookies      — Android + authenticated session
  5.  yt-dlp tv_embedded          — TV embedded player (less restricted); bgutil auto-injects PO token
  6.  yt-dlp mweb                 — Mobile web client
  7.  yt-dlp web_creator          — Creator client (different rate limiting)
  7b. yt-dlp web                  — Standard web player fingerprint
  7c. yt-dlp web_embedded         — Embedded player, different origin policies
  7d. yt-dlp tv                   — YouTube TV app protocol
  8.  nodriver (Chrome CDP)       — Real Chrome browser; generates authentic session tokens;
                                     intercepts signed CDN stream URLs; bypasses bot detection
                                     without residential proxies (free Apify alternative)
  9.  cobalt.tools (api.cobalt.tools) — API proxy; bypasses IP blocking (needs COBALT_API_TOKEN)
  10. cobalt.tools (co.wuk.sh)    — Secondary cobalt instance
  11. invidious (inv.nadeko.net)  — Open-source YouTube frontend; proxies video streams through its servers
  12. invidious (yewtu.be)        — Invidious secondary instance
  13. invidious (invidious.nerdvpn.de) — Invidious tertiary instance
  14. invidious (invidious.io)    — Invidious primary domain
  15. invidious (vid.puffyan.us)  — Long-running community instance
  16. invidious (invidious.privacydev.net) — Privacy-focused Invidious instance
  17. invidious (yt.artemislena.eu) — European Invidious instance
  18. invidious (invidious.flokinet.to) — FlokiNET-hosted Invidious instance
  19. piped (pipedapi.kavin.rocks) — Piped.video API; routes streams through Piped's proxy CDN
  20. piped (pipedapi.in.projectsegfau.lt) — Piped secondary API instance
  21. piped (piped-api.garudalinux.org) — Piped Garuda Linux instance
  22. pytubefix IOS               — Completely different Python library, IOS client
  23. pytubefix ANDROID           — pytubefix ANDROID client
  24. pytubefix TV_EMBED          — pytubefix TV_EMBED client
  25. you-get                     — Multi-platform downloader with different extraction mechanism
  26. streamlink                  — Independent stream extraction library

Environment variables:
  YTDLP_COOKIES_B64    — Base64-encoded Netscape cookies.txt for authenticated yt-dlp downloads
                         Encode your cookies file with: base64 -w 0 cookies.txt
  YTDLP_PROXY          — HTTP/SOCKS proxy URL for all yt-dlp strategies, e.g.:
                           http://user:pass@proxy.example.com:8080
                           socks5://user:pass@proxy.example.com:1080
                         Use a residential proxy service to bypass YouTube datacenter IP blocking.
                         Recommended services: Webshare (webshare.io), Smartproxy, Oxylabs
  COBALT_API_TOKEN     — cobalt.tools API key (obtain free at https://cobalt.tools/)
  YTDLP_PO_TOKEN       — YouTube Proof-of-Origin token (advanced, optional)
  YTDLP_VISITOR_DATA   — YouTube visitor data (paired with PO token)
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

try:
    import you_get  # noqa: F401
    YOU_GET_AVAILABLE = True
    logger.info("✅ you-get available (extra strategy)")
except ImportError:
    YOU_GET_AVAILABLE = False
    logger.warning("⚠️ you-get not installed — extra strategy unavailable. Add you-get to requirements.txt")

try:
    import nodriver  # noqa: F401
    NODRIVER_AVAILABLE = True
    logger.info("✅ nodriver available (browser automation strategy — free Apify alternative)")
except Exception:
    # Catches ImportError (not installed) and SyntaxError (nodriver cdp/network.py has a
    # non-UTF-8 byte in a comment that breaks Python 3.14's strict source encoding check).
    NODRIVER_AVAILABLE = False
    logger.warning("⚠️ nodriver unavailable — browser automation strategy disabled (import failed)")

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
        self.cobalt_api_token: Optional[str] = os.getenv('COBALT_API_TOKEN')
        self.proxy: Optional[str] = os.getenv('YTDLP_PROXY')
        self._setup_cookies()
        if self.proxy:
            logger.info(f"✅ Residential proxy configured: {self.proxy.split('@')[-1] if '@' in self.proxy else self.proxy}")

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
        if self.proxy:
            opts['proxy'] = self.proxy

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
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if self.cobalt_api_token:
                headers["Authorization"] = f"Api-Key {self.cobalt_api_token}"

            try:
                resp = httpx.post(
                    api_url,
                    json={"url": video_url, "videoQuality": cobalt_quality, "downloadMode": "auto"},
                    headers=headers,
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

    async def _run_piped_strategy(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
        instance: str,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Download via Piped API — progressive streams proxied through Piped's CDN.

        Piped is an alternative YouTube frontend that proxies ALL stream URLs through
        its own infrastructure (pipedproxy-*.kavin.rocks etc.), completely bypassing
        YouTube CDN IP restrictions from Render datacenter IPs.

        Uses videoStreams with videoOnly=false (progressive video+audio combined).
        """
        import re

        vid_match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
        if not vid_match:
            return None, None, f"cannot extract video ID from URL: {video_url}"
        video_id = vid_match.group(1)
        max_height = QUALITY_TO_HEIGHT.get(quality, 720)
        output_path = job_dir / "video.mp4"

        def _do_download():
            import httpx

            # Step 1: fetch stream list from Piped API
            try:
                resp = httpx.get(
                    f"{instance}/streams/{video_id}",
                    timeout=30,
                    follow_redirects=True,
                    headers={"Accept": "application/json"},
                )
            except Exception as e:
                return None, None, f"Piped API request failed: {e}"

            if resp.status_code != 200:
                return None, None, f"Piped API HTTP {resp.status_code}"

            try:
                data = resp.json()
            except Exception:
                return None, None, "Piped invalid JSON response"

            if "error" in data:
                return None, None, f"Piped error: {data['error']}"

            # Step 2: find best progressive (videoOnly=false) stream within quality limit
            # Progressive streams contain both video and audio in one file.
            video_streams = data.get("videoStreams", [])
            if not video_streams:
                return None, None, "Piped: no videoStreams in response"

            progressive = [s for s in video_streams if not s.get("videoOnly", True)]
            if not progressive:
                # Fall back to any stream if no progressive found
                progressive = video_streams

            best_stream = None
            best_height = 0
            for stream in progressive:
                h = stream.get("height", 0) or 0
                if h <= max_height and h > best_height:
                    best_height = h
                    best_stream = stream

            if best_stream is None:
                # Take the first available if nothing fits quality limit
                best_stream = progressive[0]

            stream_url = best_stream.get("url")
            if not stream_url:
                return None, None, "Piped: stream entry has no URL"

            # Step 3: download through Piped's proxied URL
            try:
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream("GET", stream_url) as resp2:
                        if resp2.status_code not in (200, 206):
                            return None, None, f"Piped stream HTTP {resp2.status_code}"
                        with open(output_path, "wb") as f:
                            for chunk in resp2.iter_bytes(65536):
                                f.write(chunk)
            except Exception as e:
                return None, None, f"Piped download failed: {e}"

            if not output_path.exists() or output_path.stat().st_size == 0:
                return None, None, "Piped: empty or missing file after download"

            metadata = VideoMetadata(
                title=data.get("title", "Unknown"),
                duration_seconds=float(data.get("duration") or 0),
                file_size_bytes=output_path.stat().st_size,
                format="mp4",
                video_id=video_id,
                view_count=int(data["views"]) if data.get("views") else None,
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
            return None, None, "Piped strategy timed out after 6 minutes"
        except Exception as e:
            return None, None, str(e)

        return result

    async def _run_you_get_strategy(
        self,
        video_url: str,
        job_dir: Path,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """Download via you-get — a multi-platform downloader with a different extraction mechanism than yt-dlp."""
        if not YOU_GET_AVAILABLE:
            return None, None, "you-get not installed"

        def _do_download():
            import you_get
            from you_get import common as you_get_common

            # Redirect you-get's output to capture errors
            import io
            import contextlib

            # you-get writes to the current directory by default; force our job dir
            output_file = job_dir / "video"

            # you-get expects sys.argv-style usage; use its download API
            try:
                you_get_common.any_download(
                    video_url,
                    output_dir=str(job_dir),
                    output_filename=str(output_file.name),
                    # Disable interactive prompts
                    **{}
                )
            except SystemExit:
                pass  # you-get calls sys.exit(0) on success
            except Exception as e:
                return None, None, f"you-get error: {e}"

            # Find the downloaded file
            candidates = sorted(
                [p for p in job_dir.glob("video*") if p.is_file() and p.stat().st_size > 0],
                key=lambda p: p.stat().st_size,
                reverse=True,
            )
            if not candidates:
                return None, None, "you-get: no output file found"

            actual_path = candidates[0]
            metadata = VideoMetadata(
                title="Unknown",
                duration_seconds=0.0,
                file_size_bytes=actual_path.stat().st_size,
                format=actual_path.suffix.lstrip(".") or "mp4",
                is_live=False,
                is_private=False,
            )
            return actual_path, metadata, None

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do_download),
                timeout=300,
            )
        except asyncio.TimeoutError:
            return None, None, "you-get strategy timed out after 5 minutes"
        except Exception as e:
            return None, None, str(e)

        return result

    async def _run_nodriver_strategy(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
    ) -> tuple[Optional[Path], Optional[VideoMetadata], Optional[str]]:
        """
        Download via nodriver (headless Chrome with DevTools Protocol).

        Architecture — free equivalent of what Apify's actor does:
          Apify:    yt-dlp → Residential proxy pool → YouTube → video
          nodriver: Real Chrome browser → YouTube → signed CDN URL → direct download

        Unlike yt-dlp which sends raw HTTP requests detectable as bots, this launches
        a real Chromium instance that generates authentic session tokens (PO token,
        botguard) only produced by real browsers.

        The signed CDN URL is captured within the same browser session/IP, so
        downloading it from the same IP avoids the 403 that rusty_ytdl was hitting.
        """
        if not NODRIVER_AVAILABLE:
            return None, None, "nodriver not installed"

        import nodriver as uc
        import httpx
        import re as _re

        output_path = job_dir / "video.mp4"
        intercepted_urls: List[str] = []
        video_title = "Unknown"

        async def _do_browser():
            nonlocal video_title
            chrome_path = os.getenv('CHROME_BIN', '/usr/bin/chromium')

            browser = await uc.start(
                headless=True,
                browser_executable_path=chrome_path,
                browser_args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--autoplay-policy=no-user-gesture-required',
                ],
            )
            try:
                tab = await browser.get(video_url)
                # Wait for page to load and player to initialise
                await tab.sleep(3)

                # Attempt to play video to trigger stream requests
                try:
                    await tab.evaluate("""
                        var v = document.querySelector('video');
                        if (v) { v.play(); }
                    """)
                    await tab.sleep(5)
                except Exception:
                    pass

                # Get video title
                try:
                    title_el = await tab.find('h1.ytd-video-primary-info-renderer', timeout=3)
                    if title_el:
                        video_title = await title_el.get_attribute('textContent') or "Unknown"
                except Exception:
                    pass

                # Capture signed googlevideo CDN URLs from rendered page source
                try:
                    page_source = await tab.get_content()
                    urls = _re.findall(
                        r'https://[a-z0-9-]+\.googlevideo\.com/videoplayback[^"\'\\s>]+',
                        page_source,
                    )
                    intercepted_urls.extend(urls)
                except Exception:
                    pass

            finally:
                try:
                    browser.stop()
                except Exception:
                    pass

        try:
            await asyncio.wait_for(
                _do_browser(),
                timeout=120,  # 2 minutes for the entire browser session
            )
        except asyncio.TimeoutError:
            return None, None, "nodriver browser session timed out after 2 minutes"
        except Exception as e:
            return None, None, f"nodriver browser error: {e}"

        if not intercepted_urls:
            return None, None, "nodriver: no video stream URLs found in page"

        logger.info(f"🌐 nodriver intercepted {len(intercepted_urls)} CDN URL(s)")

        # Deduplicate and clean URLs
        seen: set = set()
        unique_urls: List[str] = []
        for u in intercepted_urls:
            clean = u.replace('\\u0026', '&').replace('\\/', '/')
            if clean not in seen:
                seen.add(clean)
                unique_urls.append(clean)

        for attempt, stream_url in enumerate(unique_urls[:5], 1):
            try:
                logger.info(f"⬇️ nodriver: downloading stream URL {attempt}/{min(len(unique_urls), 5)}")
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream('GET', stream_url) as resp:
                        if resp.status_code not in (200, 206):
                            logger.warning(f"nodriver stream URL {attempt} returned HTTP {resp.status_code}")
                            continue
                        with open(output_path, 'wb') as f:
                            for chunk in resp.iter_bytes(65536):
                                f.write(chunk)
                if output_path.exists() and output_path.stat().st_size > 0:
                    break
            except Exception as e:
                logger.warning(f"nodriver stream URL {attempt} download failed: {e}")
                continue

        if not output_path.exists() or output_path.stat().st_size == 0:
            return None, None, "nodriver: all intercepted stream URLs failed to download"

        metadata = VideoMetadata(
            title=video_title.strip() or "Unknown",
            duration_seconds=0.0,
            file_size_bytes=output_path.stat().st_size,
            format="mp4",
            is_live=False,
            is_private=False,
        )
        return output_path, metadata, None

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

        # --- Proxy-first strategies (prepended when YTDLP_PROXY is set) ---
        # Residential proxies bypass YouTube's datacenter IP blocking entirely.
        # These are tried first because they have the highest success probability.
        if self.proxy:
            strategies.append(("yt-dlp ios+proxy", "ytdlp", {
                "player_clients": ["ios"], "use_cookies": False, "skip_webpage": True
            }))
            strategies.append(("yt-dlp android+proxy", "ytdlp", {
                "player_clients": ["android"], "use_cookies": False, "skip_webpage": True
            }))
            strategies.append(("yt-dlp web+proxy", "ytdlp", {
                "player_clients": ["web"], "use_cookies": has_cookies, "skip_webpage": not has_cookies
            }))

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

        # Strategy 7b: web client — standard web player, different fingerprint than mobile/app clients
        strategies.append(("yt-dlp web", "ytdlp", {
            "player_clients": ["web"],
            "use_cookies": has_cookies,
            "skip_webpage": not has_cookies,
        }))

        # Strategy 7c: web_embedded client — embedded player, different origin policies
        strategies.append(("yt-dlp web_embedded", "ytdlp", {
            "player_clients": ["web_embedded"],
            "use_cookies": has_cookies,
            "skip_webpage": not has_cookies,
        }))

        # Strategy 7d: tv client (distinct from tv_embedded) — YouTube TV app protocol
        strategies.append(("yt-dlp tv", "ytdlp", {
            "player_clients": ["tv"],
            "use_cookies": has_cookies,
            "skip_webpage": not has_cookies,
        }))

        # --- Browser automation strategy (free Apify alternative) ---
        # nodriver launches real Chromium via CDP — generates authentic YouTube session tokens
        # that raw HTTP requests (yt-dlp, pytubefix, etc.) cannot produce on datacenter IPs.
        # The signed CDN URL is intercepted and downloaded from the same IP/session → no 403.
        if NODRIVER_AVAILABLE:
            strategies.append(("nodriver (Chrome CDP)", "nodriver", {}))

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
        # Multiple instances increase chances of finding one accessible from Render's datacenter IP.
        for inv_instance in [
            "https://inv.nadeko.net",
            "https://yewtu.be",
            "https://invidious.nerdvpn.de",
            "https://invidious.io",
            "https://vid.puffyan.us",
            "https://invidious.privacydev.net",
            "https://yt.artemislena.eu",
            "https://invidious.flokinet.to",
        ]:
            host = inv_instance.replace("https://", "")
            strategies.append((f"invidious ({host})", "invidious", {"instance": inv_instance}))

        # Piped — alternative YouTube frontend with its own proxy CDN (pipedproxy-*.kavin.rocks).
        # Streams are served through Piped's infrastructure, bypassing YouTube CDN IP restrictions.
        for piped_instance in [
            "https://pipedapi.kavin.rocks",
            "https://pipedapi.in.projectsegfau.lt",
            "https://piped-api.garudalinux.org",
        ]:
            host = piped_instance.replace("https://", "")
            strategies.append((f"piped ({host})", "piped", {"instance": piped_instance}))

        # --- pytubefix strategies (completely different Python library) ---
        if PYTUBEFIX_AVAILABLE:
            strategies.append(("pytubefix IOS", "pytubefix", {"client_name": "IOS"}))
            strategies.append(("pytubefix ANDROID", "pytubefix", {"client_name": "ANDROID"}))
            strategies.append(("pytubefix TV_EMBED", "pytubefix", {"client_name": "TV_EMBED"}))

        # --- you-get strategy (independent multi-platform downloader, different from yt-dlp) ---
        if YOU_GET_AVAILABLE:
            strategies.append(("you-get", "you_get", {}))

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
                elif kind == "piped":
                    file_path, metadata, error_msg = await self._run_piped_strategy(
                        video_url, job_dir, quality,
                        instance=kwargs["instance"],
                    )
                elif kind == "pytubefix":
                    file_path, metadata, error_msg = await self._run_pytubefix_strategy(
                        video_url, job_dir, quality,
                        client_name=kwargs["client_name"],
                    )
                elif kind == "you_get":
                    file_path, metadata, error_msg = await self._run_you_get_strategy(
                        video_url, job_dir,
                    )
                elif kind == "nodriver":
                    file_path, metadata, error_msg = await self._run_nodriver_strategy(
                        video_url, job_dir, quality,
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
