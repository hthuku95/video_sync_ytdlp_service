"""
Integration tests for WebshareProxyManager.

These tests hit the real Webshare API (or download link) and verify that:
  - Proxies are fetched and cached
  - Returned URLs have the expected format
  - Playwright proxy dicts have the required keys
  - Round-robin rotation cycles through different proxies
"""

import asyncio
import os
import re

import pytest

from app.proxy_manager import WebshareProxyManager


# ─── Helpers ─────────────────────────────────────────────────────────────────

PROXY_URL_PATTERN = re.compile(
    r"^http://[^:@]+:[^:@]+@[^:@]+:[0-9]+$"
)


def require_webshare_env():
    """Skip test if neither Webshare env var is set."""
    if not os.getenv("WEBSHARE_DOWNLOAD_LINK") and not os.getenv("WEBSHARE_YTDLAPI_API_KEY"):
        pytest.skip("WEBSHARE_DOWNLOAD_LINK and WEBSHARE_YTDLAPI_API_KEY not set")


# ─── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proxy_manager_fetch():
    """fetch() from Webshare should populate self._proxies with > 0 entries."""
    require_webshare_env()
    pm = WebshareProxyManager()
    await pm.refresh()
    assert len(pm._proxies) > 0, (
        f"Expected at least 1 proxy after refresh, got {len(pm._proxies)}. "
        "Check WEBSHARE_DOWNLOAD_LINK or WEBSHARE_YTDLAPI_API_KEY."
    )
    print(f"\n✅ Loaded {len(pm._proxies)} proxies from Webshare")


@pytest.mark.asyncio
async def test_get_proxy_url_format():
    """get_proxy_url() should return a URL matching http://user:pass@ip:port."""
    require_webshare_env()
    pm = WebshareProxyManager()
    await pm.refresh()

    if not pm._proxies:
        pytest.skip("No proxies loaded — check Webshare credentials")

    url = pm.get_proxy_url()
    assert url is not None, "get_proxy_url() returned None despite proxies being loaded"
    assert PROXY_URL_PATTERN.match(url), (
        f"Proxy URL '{url}' does not match expected format http://user:pass@ip:port"
    )
    print(f"\n✅ Proxy URL format OK: {url.split('@')[-1]}")  # only print host:port, not credentials


@pytest.mark.asyncio
async def test_get_playwright_proxy_fields():
    """get_playwright_proxy() should return a dict with server, username, password."""
    require_webshare_env()
    pm = WebshareProxyManager()
    await pm.refresh()

    if not pm._proxies:
        pytest.skip("No proxies loaded — check Webshare credentials")

    proxy = pm.get_playwright_proxy()
    assert proxy is not None, "get_playwright_proxy() returned None despite proxies being loaded"
    assert "server" in proxy, "Playwright proxy dict missing 'server' key"
    assert "username" in proxy, "Playwright proxy dict missing 'username' key"
    assert "password" in proxy, "Playwright proxy dict missing 'password' key"
    assert proxy["server"].startswith("http://"), (
        f"proxy['server'] should start with 'http://', got: {proxy['server']}"
    )
    print(f"\n✅ Playwright proxy dict OK: server={proxy['server']}")


@pytest.mark.asyncio
async def test_round_robin_rotation():
    """
    get_proxy_url() should cycle through different proxy credentials on repeated calls.

    Webshare backbone proxies share a single gateway host (p.webshare.io:80) but
    rotate via numbered usernames (e.g. fqsyhqpp-1, fqsyhqpp-2, ...) — so we
    check that usernames differ across calls, not hosts.
    """
    require_webshare_env()
    pm = WebshareProxyManager()
    await pm.refresh()

    if len(pm._proxies) < 2:
        pytest.skip(f"Need at least 2 proxies for rotation test, only {len(pm._proxies)} loaded")

    # Sample a small set of calls (don't iterate all 215k proxies)
    sample = [pm.get_proxy_url() for _ in range(min(10, len(pm._proxies)))]

    # Extract the username portion (http://USERNAME:pass@host:port)
    def _username(url):
        # url = "http://user:pass@host:port"
        creds = url.split("@")[0].replace("http://", "")
        return creds.split(":")[0]

    unique_users = {_username(u) for u in sample if u}
    assert len(unique_users) >= 2, (
        f"Expected at least 2 unique usernames in 10 calls, "
        f"but only got: {unique_users}. "
        f"Check that WEBSHARE_DOWNLOAD_LINK lists multiple proxy entries."
    )
    print(f"\n✅ Round-robin rotation working: {len(unique_users)} unique usernames in {len(sample)} calls")


@pytest.mark.asyncio
async def test_graceful_no_credentials():
    """proxy_manager with no env vars should return None without crashing."""
    pm = WebshareProxyManager()
    pm._download_link = None
    pm._api_key = None
    # Should not raise
    await pm.refresh()
    assert pm._proxies == [], "Expected empty proxy list when no credentials set"
    assert pm.get_proxy_url() is None, "get_proxy_url() should return None when no proxies loaded"
    assert pm.get_playwright_proxy() is None, "get_playwright_proxy() should return None when no proxies loaded"
    print("\n✅ Graceful no-credentials behaviour confirmed")
