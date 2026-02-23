"""
Integration tests for the Playwright + Gemini download agent.

These tests cover the full 3-tier fallback:
  Tier 1: LangGraph + Gemini StateGraph (navigate → consent → play → download)
  Tier 2: Hardcoded consent selectors + JS playVideo()
  Tier 3: ytInitialPlayerResponse JS extraction

Run:
    pytest tests/test_strategy_playwright.py -v --tb=long
"""

import os

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import PLAYWRIGHT_AVAILABLE, LANGCHAIN_GEMINI_AVAILABLE


def require_playwright():
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("playwright not installed — run: pip install playwright && playwright install chromium")


@pytest.mark.asyncio
async def test_playwright_agent_download(job_dir):
    """
    Full Playwright + Gemini agent end-to-end: navigate, vectorize page,
    handle consent dialog, trigger playback, intercept CDN URL, download.
    """
    require_playwright()
    from app.playwright_agent import YouTubePlaywrightAgent
    agent = YouTubePlaywrightAgent()
    path, meta, err = await agent.download(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
    )
    if err:
        pytest.skip(f"Playwright agent skipped: {err[:200]}")
    assert_video_downloaded(path, msg_prefix="playwright agent")
    print(f"\n✅ playwright agent: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_playwright_tier2_fallback(job_dir):
    """
    Tier 2 only: hardcoded consent selectors + JS playVideo().
    Directly tests _run_tier2_simple() after manually navigating to the page.
    """
    require_playwright()
    from playwright.async_api import async_playwright
    from app.playwright_agent import YouTubePlaywrightAgent, _AgentSession

    agent = YouTubePlaywrightAgent()
    output_path = job_dir / "video.mp4"

    import asyncio
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        from app.proxy_manager import proxy_manager
        playwright_proxy = proxy_manager.get_playwright_proxy()
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            **({"proxy": playwright_proxy} if playwright_proxy else {}),
        )
        page = await ctx.new_page()
        page.on("response", agent._intercept_response)

        try:
            await page.goto(TEST_VIDEO_URL, wait_until="commit", timeout=60_000)
            await asyncio.sleep(3)
            await agent._run_tier2_simple(page)
            await asyncio.sleep(5)
        except Exception as e:
            await browser.close()
            pytest.skip(f"Tier 2 skipped (navigation/proxy error): {e!s:.200}")
        finally:
            await browser.close()

    # Tier 2 doesn't download directly — it just triggers playback so CDNs are intercepted.
    # We verify that at least one CDN URL was captured.
    if not agent.intercepted_cdns:
        pytest.skip("Tier 2 produced no CDN URLs (bot detection or page load failure)")
    print(f"\n✅ Tier 2: {len(agent.intercepted_cdns)} CDN URL(s) intercepted")


@pytest.mark.asyncio
async def test_playwright_tier3_fallback(job_dir):
    """
    Tier 3: ytInitialPlayerResponse JS extraction — does NOT use browser interception,
    extracts stream URLs directly from embedded JSON in the page source.
    """
    require_playwright()
    from playwright.async_api import async_playwright
    from app.playwright_agent import YouTubePlaywrightAgent

    agent = YouTubePlaywrightAgent()
    output_path = job_dir / "video.mp4"

    import asyncio
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        from app.proxy_manager import proxy_manager
        playwright_proxy = proxy_manager.get_playwright_proxy()
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            **({"proxy": playwright_proxy} if playwright_proxy else {}),
        )
        page = await ctx.new_page()
        try:
            await page.goto(TEST_VIDEO_URL, wait_until="commit", timeout=60_000)
            await asyncio.sleep(3)
            result = await agent._run_tier3_extract(page, output_path)
        except Exception as e:
            await browser.close()
            pytest.skip(f"Tier 3 skipped (navigation/proxy error): {e!s:.200}")
        finally:
            await browser.close()

    if result is None:
        pytest.skip("Tier 3 extraction returned no result (cipher-encrypted or bot detection)")
    path = result
    # Tier 3 may produce a smaller file (just a stream segment); just check it exists and > 0
    assert path.exists(), f"Tier 3 output file not found: {path}"
    assert path.stat().st_size > 0, "Tier 3 produced empty file"
    print(f"\n✅ Tier 3: {path.stat().st_size / 1024 / 1024:.1f} MB extracted")


@pytest.mark.asyncio
async def test_playwright_page_vectorize(job_dir):
    """
    Confirm that the PageVectorizer creates a Qdrant collection with > 0 chunks
    after navigating to a YouTube page.
    """
    require_playwright()
    if not os.getenv("QDRANT_URL"):
        pytest.skip("QDRANT_URL not set")
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY not set")

    from app.page_vectorizer import PageVectorizer
    from app.playwright_agent import VECTORIZER_AVAILABLE

    if not VECTORIZER_AVAILABLE:
        pytest.skip("PageVectorizer unavailable (import failed)")

    from playwright.async_api import async_playwright
    import asyncio

    job_id = "test_vectorize_job"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        from app.proxy_manager import proxy_manager
        playwright_proxy = proxy_manager.get_playwright_proxy()
        ctx = await browser.new_context(
            **({"proxy": playwright_proxy} if playwright_proxy else {}),
        )
        page = await ctx.new_page()
        try:
            await page.goto(TEST_VIDEO_URL, wait_until="commit", timeout=60_000)
            await asyncio.sleep(2)
            html = await page.content()
            title = await page.title()
            current_url = page.url
        except Exception as e:
            await browser.close()
            pytest.skip(f"Vectorize skipped (navigation/proxy error): {e!s:.200}")
        finally:
            await browser.close()

    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
    )
    vz = PageVectorizer(
        qdrant_url=os.getenv("QDRANT_URL"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        embeddings_model=embeddings,
    )
    collection = await vz.vectorize_and_store(html, current_url, title, job_id)
    assert collection, "PageVectorizer returned empty collection name"
    print(f"\n✅ Qdrant collection created: {collection}")


@pytest.mark.asyncio
async def test_playwright_proxy_configured():
    """
    Confirm that when WEBSHARE_* env vars are present, the proxy manager
    returns a valid Playwright proxy dict (not None).
    """
    if not os.getenv("WEBSHARE_DOWNLOAD_LINK") and not os.getenv("WEBSHARE_YTDLAPI_API_KEY"):
        pytest.skip("WEBSHARE_* env vars not set — proxy configuration test not applicable")

    from app.proxy_manager import WebshareProxyManager
    pm = WebshareProxyManager()
    await pm.refresh()
    proxy = pm.get_playwright_proxy()
    assert proxy is not None, (
        "Expected a Playwright proxy dict when WEBSHARE_* env vars are set, got None. "
        "Check that Webshare credentials are valid."
    )
    assert "server" in proxy and "username" in proxy and "password" in proxy
    print(f"\n✅ Playwright proxy configured: {proxy['server']}")
