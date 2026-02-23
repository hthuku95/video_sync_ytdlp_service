"""
Webshare.io Residential Proxy Manager

Fetches residential proxies from Webshare.io via:
  1. WEBSHARE_DOWNLOAD_LINK — pre-authenticated direct download URL (fastest)
  2. WEBSHARE_YTDLAPI_API_KEY — REST API fallback

Proxy URL format from download link: IP:port:username:password (one per line)

Usage:
    from .proxy_manager import proxy_manager

    proxy_url = proxy_manager.get_proxy_url()
    # -> "http://user:pass@ip:port" or None

    playwright_proxy = proxy_manager.get_playwright_proxy()
    # -> {"server": "http://ip:port", "username": "...", "password": "..."} or None
"""

import asyncio
import logging
import os
import random
from typing import Dict, List, Optional

# Cap in-memory proxy list to avoid OOM on 512 MB Render instances.
# 215k proxies × ~700 bytes per Python dict ≈ 145 MB — too much.
# 1 000 proxies give ample rotation while using < 1 MB.
_MAX_PROXIES_IN_MEMORY = 1_000

logger = logging.getLogger(__name__)


class WebshareProxyManager:
    """
    Fetches residential proxies from Webshare.io and rotates through them.

    Uses WEBSHARE_DOWNLOAD_LINK (pre-authenticated URL, fastest) with
    WEBSHARE_YTDLAPI_API_KEY (API auth) as fallback.

    Thread-safe round-robin rotation via a simple index counter.
    """

    def __init__(self) -> None:
        self._proxies: List[Dict[str, str]] = []
        self._index: int = 0
        self._download_link: Optional[str] = os.getenv("WEBSHARE_DOWNLOAD_LINK")
        self._api_key: Optional[str] = os.getenv("WEBSHARE_YTDLAPI_API_KEY")

    # ─────────────────────────────────────────────────────────────────────────
    # Internal fetch helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_download_link_response(self, text: str) -> List[Dict[str, str]]:
        """
        Parse a Webshare proxy list download response.
        Each line: ip:port:username:password

        Randomly samples up to _MAX_PROXIES_IN_MEMORY lines to avoid OOM
        on resource-constrained servers (512 MB Render instances).
        """
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if len(lines) > _MAX_PROXIES_IN_MEMORY:
            lines = random.sample(lines, _MAX_PROXIES_IN_MEMORY)

        proxies: List[Dict[str, str]] = []
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 4 and all(parts[:4]):
                ip, port, username, password = parts[0], parts[1], parts[2], parts[3]
                proxies.append({
                    "server": f"http://{ip}:{port}",
                    "username": username,
                    "password": password,
                })
        return proxies

    def _parse_api_response(self, data: dict) -> List[Dict[str, str]]:
        """
        Parse Webshare REST API /proxy/list/ response.
        JSON: {"results": [{"proxy_address": "...", "port": ..., "username": "...", "password": "..."}]}
        """
        proxies: List[Dict[str, str]] = []
        for item in data.get("results", []):
            ip = item.get("proxy_address", "")
            port = item.get("port", "")
            username = item.get("username", "")
            password = item.get("password", "")
            if ip and port and username and password:
                proxies.append({
                    "server": f"http://{ip}:{port}",
                    "username": username,
                    "password": password,
                })
        return proxies

    # ─────────────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────────────

    async def refresh(self) -> None:
        """
        Fetch fresh proxy list from Webshare and cache in self._proxies.
        Tries WEBSHARE_DOWNLOAD_LINK first, then WEBSHARE_YTDLAPI_API_KEY.
        Gracefully handles missing env vars or network errors.
        """
        import httpx  # local import — httpx is already a project dependency

        proxies: List[Dict[str, str]] = []

        # Strategy 1: pre-authenticated download link
        if self._download_link:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(self._download_link)
                    if resp.status_code == 200:
                        proxies = self._parse_download_link_response(resp.text)
                        if proxies:
                            logger.info(
                                f"✅ Webshare proxy manager: loaded {len(proxies)} proxies "
                                f"via download link (sampled from full list)"
                            )
            except Exception as e:
                logger.warning(f"⚠️ Webshare download link failed: {e}")

        # Strategy 2: REST API with API key
        if not proxies and self._api_key:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        "https://proxy.webshare.io/api/v2/proxy/list/",
                        params={"mode": "direct", "page_size": 100},
                        headers={"Authorization": f"Token {self._api_key}"},
                    )
                    if resp.status_code == 200:
                        proxies = self._parse_api_response(resp.json())
                        if proxies:
                            logger.info(
                                f"✅ Webshare proxy manager: loaded {len(proxies)} proxies "
                                f"via API key"
                            )
                    else:
                        logger.warning(
                            f"⚠️ Webshare API returned HTTP {resp.status_code}: {resp.text[:200]}"
                        )
            except Exception as e:
                logger.warning(f"⚠️ Webshare API fallback failed: {e}")

        if not proxies:
            if not self._download_link and not self._api_key:
                logger.info(
                    "ℹ️ Webshare proxy manager: WEBSHARE_DOWNLOAD_LINK and "
                    "WEBSHARE_YTDLAPI_API_KEY not set — running without residential proxies"
                )
            else:
                logger.warning(
                    "⚠️ Webshare proxy manager: failed to load proxies — "
                    "strategies will run without residential proxies"
                )

        self._proxies = proxies
        self._index = 0  # reset round-robin on refresh

    def get_proxy_url(self) -> Optional[str]:
        """
        Return the next proxy URL in round-robin rotation.
        Format: http://username:password@ip:port

        Returns None if no proxies are loaded (callers must handle None).
        """
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index = (self._index + 1) % len(self._proxies)
        server = proxy["server"]  # "http://ip:port"
        # Strip scheme for URL embedding
        host_port = server.removeprefix("http://").removeprefix("https://")
        return f"http://{proxy['username']}:{proxy['password']}@{host_port}"

    def get_playwright_proxy(self) -> Optional[Dict[str, str]]:
        """
        Return the next proxy as a Playwright-compatible proxy dict.
        Format: {"server": "http://ip:port", "username": "...", "password": "..."}

        Returns None if no proxies are loaded.
        """
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index = (self._index + 1) % len(self._proxies)
        return {
            "server": proxy["server"],
            "username": proxy["username"],
            "password": proxy["password"],
        }

    async def auto_refresh_loop(self) -> None:
        """
        Background task: refresh proxy list every 3600 seconds (1 hour).
        Call once at startup and let it run indefinitely.
        """
        while True:
            await asyncio.sleep(3600)
            logger.info("🔄 Webshare proxy manager: hourly refresh...")
            await self.refresh()


# Module-level singleton — import this everywhere
proxy_manager = WebshareProxyManager()
