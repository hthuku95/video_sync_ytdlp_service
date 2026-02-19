"""
Playwright + Gemini YouTube Download Agent

Uses event-driven Playwright network interception to capture signed YouTube CDN URLs,
and optional Gemini LLM reasoning (via LangGraph ReAct agent) to handle consent dialogs
and trigger playback intelligently.

Why this works where yt-dlp fails:
  yt-dlp:     raw HTTP → YouTube fingerprints as bot from datacenter IP → blocked
  Playwright: real Chromium browser → YouTube JS player generates authentic PO tokens
              → YouTube serves signed CDN URLs → page.on("response") captures them
              → httpx downloads from SAME server IP the browser used → no 403

Why Playwright > nodriver (disabled Strategy 8):
  nodriver: searched rendered HTML for CDN URLs (fragile; URLs may not appear in HTML)
  Playwright: page.on("response", handler) fires for EVERY network response in real-time,
              before HTML is updated — reliably catches manifests, init segments, media

Agent architecture (LangGraph ReAct):
  create_react_agent from langgraph.prebuilt replaces the deprecated
  initialize_agent / AgentType / ConversationBufferMemory pattern from langchain-classic.
  Tools are defined as nested @tool-decorated closures that bridge sync LangGraph →
  async Playwright via asyncio.run_coroutine_threadsafe.
"""

import asyncio
import base64
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ── Availability flags ──────────────────────────────────────────────────────

PLAYWRIGHT_AVAILABLE = False
LANGCHAIN_GEMINI_AVAILABLE = False

try:
    from playwright.async_api import (
        Browser, BrowserContext, Page, Response, async_playwright,
    )
    PLAYWRIGHT_AVAILABLE = True
    logger.info("✅ playwright available (Playwright CDN interception strategy active)")
except ImportError:
    logger.warning(
        "⚠️ playwright not installed — run: pip install playwright && playwright install chromium"
    )

try:
    from bs4 import BeautifulSoup
    from langgraph.prebuilt import create_react_agent
    from langchain_core.tools import tool
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
    LANGCHAIN_GEMINI_AVAILABLE = True
    logger.info("✅ langchain-google-genai + LangGraph available (Gemini ReAct agent enabled)")
except Exception:
    logger.warning(
        "⚠️ langchain-google-genai/LangGraph unavailable — falling back to simple JS interaction"
    )


# ── PageState ────────────────────────────────────────────────────────────────

@dataclass
class PageState:
    """Snapshot of browser page state — used by agent tools for page understanding."""
    url: str
    title: str
    html: str
    screenshot_b64: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    vector_store: Any = None  # FAISS instance, set by _vectorize_page


# ── Agent class ──────────────────────────────────────────────────────────────

class YouTubePlaywrightAgent:
    """
    Playwright + Gemini browser agent for YouTube CDN URL interception and download.
    Instantiate fresh per download call (holds mutable per-session state).
    """

    def __init__(self) -> None:
        self.intercepted_cdns: List[str] = []  # video-MIME URLs prepended, audio-only appended
        self.page: Optional[Page] = None
        self.page_state: Optional[PageState] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Gemini LLM + embeddings (optional — agent degrades gracefully if absent)
        self._llm = None
        self._embeddings = None
        api_key = os.getenv("GOOGLE_API_KEY")
        if LANGCHAIN_GEMINI_AVAILABLE and api_key:
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=api_key,
                temperature=0,
            )
            self._embeddings = GoogleGenerativeAIEmbeddings(
                model="models/text-embedding-004",
                google_api_key=api_key,
            )

    # ── Network interception ──────────────────────────────────────────────────

    def _intercept_response(self, response: "Response") -> None:
        """
        Synchronous Playwright event handler — fires for EVERY network response.
        Registered via page.on("response", ...) BEFORE page.goto() so we catch
        the very first CDN requests YouTube's player makes during page load.
        Sync handler is safe: only appends to a list, no I/O.
        """
        url = response.url
        if "googlevideo.com/videoplayback" not in url:
            return
        if url in self.intercepted_cdns:
            return
        # Skip YouTube's SABR (Server-side ABR) protocol URLs — these require a binary
        # request body to specify desired segments; a plain GET returns only ~31 bytes.
        if "sabr=1" in url:
            return
        # Video-stream URLs prepended (higher priority for download); audio-only appended
        if "mime=video" in url or "mime%3Dvideo" in url:
            self.intercepted_cdns.insert(0, url)
        else:
            self.intercepted_cdns.append(url)
        logger.info(
            f"[playwright] Captured CDN URL #{len(self.intercepted_cdns)}: {url[:80]}..."
        )

    # ── Page state capture ────────────────────────────────────────────────────

    async def _capture_page_state(self) -> None:
        """Snapshot current page HTML/title/screenshot and optionally vectorize for agent tools."""
        try:
            title = await self.page.title()
            html = await self.page.content()
            url = self.page.url

            screenshot_b64 = None
            try:
                raw = await self.page.screenshot(type="png", full_page=False)
                screenshot_b64 = base64.b64encode(raw).decode()
            except Exception:
                pass

            self.page_state = PageState(
                url=url, title=title, html=html, screenshot_b64=screenshot_b64,
            )

            # Vectorize off event loop (CPU-bound BeautifulSoup + FAISS build)
            if self._embeddings:
                vs = await asyncio.get_event_loop().run_in_executor(
                    None, self._vectorize_page, html, url, title
                )
                self.page_state.vector_store = vs
        except Exception as e:
            logger.warning(f"[playwright] _capture_page_state error: {e}")

    def _vectorize_page(self, html: str, url: str, title: str) -> Optional[Any]:
        """
        Build FAISS vector store from page DOM elements.
        Enables Gemini agent to semantically search: 'find consent accept button'.
        Synchronous — must run in executor.
        """
        if not self._embeddings:
            return None
        try:
            soup = BeautifulSoup(html, "html.parser")
            chunks: List[str] = []
            for el in soup.find_all(
                ["button", "a", "input", "select", "video"], limit=200
            ):
                text = el.get_text(strip=True)[:120]
                attrs = {
                    k: v for k, v in el.attrs.items()
                    if k in ("id", "class", "aria-label", "role", "data-testid")
                }
                if text or attrs:
                    chunks.append(f"[{el.name}] text={text!r} attrs={attrs}")
            if not chunks:
                return None
            docs = [
                Document(page_content=c, metadata={"url": url, "title": title})
                for c in chunks
            ]
            return FAISS.from_documents(docs, self._embeddings)
        except Exception as e:
            logger.warning(f"[playwright] _vectorize_page error: {e}")
            return None

    def _get_page_context_tool(
        self, query: str = "interactive elements buttons consent dialog"
    ) -> str:
        """FAISS semantic search of current page state."""
        if not self.page_state:
            return "No page loaded yet."
        header = f"URL: {self.page_state.url}\nTitle: {self.page_state.title}\n"
        if not self.page_state.vector_store:
            # Vector store unavailable (embedding API error) — return raw page title as context
            return header + "(Vector store unavailable — use hardcoded consent selectors)"
        try:
            results = self.page_state.vector_store.similarity_search_with_score(query, k=6)
            lines = [f"  (score={s:.2f}) {d.page_content}" for d, s in results]
            return header + "Relevant elements:\n" + "\n".join(lines)
        except Exception as e:
            return header + f"(Vector search error: {e})"

    # ── Agent phase (LangGraph ReAct) ─────────────────────────────────────────

    async def _run_agent_phase(self) -> None:
        """
        Gemini-powered LangGraph ReAct agent to dismiss consent dialogs and trigger playback.

        Uses create_react_agent from langgraph.prebuilt — the modern replacement for the
        deprecated initialize_agent / AgentType / ConversationBufferMemory pattern.

        Async/sync bridge: agent.invoke() is synchronous; Playwright Page is async.
        Tools use asyncio.run_coroutine_threadsafe(coro, loop) to call async Playwright
        methods from the synchronous thread that LangGraph executes in.
        """
        if not LANGCHAIN_GEMINI_AVAILABLE or not self._llm:
            await self._simple_interaction_phase()
            return

        loop = self._loop
        page = self.page
        agent_self = self  # explicit capture for inner closures

        # ── Tool definitions ──────────────────────────────────────────────────
        # Nested @tool closures capture page/loop/agent_self without globals.
        # Type annotations + docstring Args section are required for LangGraph
        # to auto-generate the JSON schema exposed to the LLM.

        @tool
        def get_page_context(query: str) -> str:
            """Search current page elements to understand what's on screen.

            Args:
                query: Natural language search, e.g. 'accept button' or 'consent dialog'
            """
            return agent_self._get_page_context_tool(query)

        @tool
        def click_element(selector: str) -> str:
            """Click a DOM element by CSS selector.

            Args:
                selector: Valid CSS selector, e.g. 'button[aria-label*=\"Accept\"]'
            """
            fut = asyncio.run_coroutine_threadsafe(
                page.click(selector, timeout=5_000), loop
            )
            try:
                fut.result(timeout=10)
                return f"Clicked: {selector}"
            except Exception as e:
                return f"Click failed ({selector}): {e}"

        @tool
        def execute_javascript(code: str) -> str:
            """Execute a JavaScript expression in the browser.

            Args:
                code: JS expression to evaluate, e.g. "document.querySelector('video')?.play()"
            """
            fut = asyncio.run_coroutine_threadsafe(page.evaluate(code), loop)
            try:
                res = fut.result(timeout=10)
                return f"JS result: {res}"
            except Exception as e:
                return f"JS error: {e}"

        @tool
        def get_intercepted_urls(query: str) -> str:
            """List YouTube CDN video URLs captured by the network interceptor so far.

            Args:
                query: Unused, pass empty string
            """
            if not agent_self.intercepted_cdns:
                return "No CDN URLs captured yet — playback may not have started."
            return (
                f"Captured {len(agent_self.intercepted_cdns)} CDN URLs:\n"
                + "\n".join(agent_self.intercepted_cdns[:3])
            )

        # ── Build and invoke LangGraph agent ─────────────────────────────────
        tools = [get_page_context, click_element, execute_javascript, get_intercepted_urls]
        # create_react_agent returns a compiled graph ready for .invoke()
        agent = create_react_agent(model=self._llm, tools=tools)

        instruction = (
            "You control a Chrome browser that has loaded a YouTube video page. "
            "Goal: trigger video playback so the browser fetches YouTube CDN stream URLs. "
            "Steps: "
            "1) Call get_page_context with 'accept button consent dialog' to see the page. "
            "2) If a cookie consent or 'Accept' dialog is visible, call click_element to dismiss it. "
            "3) Call execute_javascript with: document.querySelector('video')?.play() "
            "4) Call get_intercepted_urls to confirm CDN URLs were captured. "
            "Stop after step 4. Do NOT navigate away from the current page."
        )

        def _run_sync() -> None:
            try:
                agent.invoke(
                    {"messages": [{"role": "user", "content": instruction}]},
                    config={"recursion_limit": 12},
                )
            except Exception as e:
                logger.warning(f"[playwright] Gemini agent error (non-critical): {e}")

        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _run_sync),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[playwright] Agent phase timed out after 60s — using simple fallback")
            await self._simple_interaction_phase()

    # ── Simple interaction (no-LLM fallback) ─────────────────────────────────

    async def _simple_interaction_phase(self) -> None:
        """
        Hardcoded fallback when Gemini is unavailable or rate-limited.
        Dismisses known YouTube/Google consent patterns, then triggers playback via JS.
        """
        consent_selectors = [
            "button[aria-label*='Accept']",
            "button[aria-label*='Agree']",
            "#introAgreeButton",
            "form[action*='consent'] button",
            ".eom-button-row button:first-child",
            "tp-yt-paper-button#agree-button",
        ]
        for sel in consent_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.click()
                    logger.info(f"[playwright] Dismissed consent via: {sel}")
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        # Trigger via YouTube's internal player API first, then HTML5 video fallback
        try:
            await self.page.evaluate("""
                (function() {
                    var p = document.getElementById('movie_player');
                    if (p && p.playVideo) { p.playVideo(); return 'yt-api'; }
                    var v = document.querySelector('video');
                    if (v) { v.play(); return 'html5'; }
                    return 'no-player-found';
                })();
            """)
            logger.info("[playwright] JS playback trigger sent")
        except Exception as e:
            logger.warning(f"[playwright] JS playback trigger error: {e}")

    # ── Main download entrypoint ──────────────────────────────────────────────

    async def download(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
    ) -> tuple[Optional[Path], Any, Optional[str]]:
        """
        Full download flow.
        Returns (file_path, VideoMetadata, None) on success.
        Returns (None, None, error_string) on failure.
        """
        from .models import VideoMetadata

        if not PLAYWRIGHT_AVAILABLE:
            return None, None, "playwright not installed"

        output_path = job_dir / "video.mp4"
        self.intercepted_cdns = []

        # ── Browser session ──────────────────────────────────────────────────
        try:
            async with async_playwright() as pw:
                browser: Browser = await pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                ctx: BrowserContext = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )
                self.page = await ctx.new_page()
                self._loop = asyncio.get_event_loop()

                # CRITICAL: register BEFORE goto() — never miss early CDN requests
                self.page.on("response", self._intercept_response)

                logger.info(f"[playwright] Navigating to {video_url}")
                await self.page.goto(
                    video_url, wait_until="domcontentloaded", timeout=60_000
                )

                await self._capture_page_state()

                if LANGCHAIN_GEMINI_AVAILABLE and self._llm:
                    await self._run_agent_phase()
                else:
                    await self._simple_interaction_phase()

                # If the Gemini agent failed (e.g. 429 rate-limit) without triggering
                # playback, fall back to the hardcoded simple interaction.
                if not self.intercepted_cdns:
                    logger.info(
                        "[playwright] No CDN URLs after agent phase — running simple interaction fallback"
                    )
                    await self._simple_interaction_phase()

                # Let CDN stream responses arrive after playback starts
                logger.info("[playwright] Waiting for CDN stream responses...")
                await asyncio.sleep(8)

                video_title = self.page_state.title if self.page_state else "Unknown"
                await browser.close()

        except asyncio.TimeoutError:
            return None, None, "playwright: browser session timed out"
        except Exception as e:
            return None, None, f"playwright browser error: {e}"

        if not self.intercepted_cdns:
            return None, None, "playwright: no CDN URLs intercepted after playback"

        logger.info(
            f"[playwright] {len(self.intercepted_cdns)} CDN URLs captured — attempting download"
        )

        # ── Download (same IP as browser → URL IP-lock matches) ──────────────
        import httpx

        for i, cdn_url in enumerate(self.intercepted_cdns[:5], 1):
            try:
                logger.info(
                    f"[playwright] Downloading CDN URL {i}/{min(len(self.intercepted_cdns), 5)}"
                )
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream("GET", cdn_url) as resp:
                        if resp.status_code not in (200, 206):
                            logger.warning(
                                f"[playwright] CDN URL {i} returned HTTP {resp.status_code}"
                            )
                            continue
                        with open(output_path, "wb") as f:
                            for chunk in resp.iter_bytes(65536):
                                f.write(chunk)

                if output_path.exists() and output_path.stat().st_size > 0:
                    size_mb = output_path.stat().st_size / 1024 / 1024
                    logger.info(f"[playwright] ✅ Downloaded {size_mb:.1f} MB from CDN URL {i}")
                    metadata = VideoMetadata(
                        title=video_title.replace(" - YouTube", "").strip() or "Unknown",
                        duration_seconds=0.0,
                        file_size_bytes=output_path.stat().st_size,
                        format="mp4",
                        is_live=False,
                        is_private=False,
                    )
                    return output_path, metadata, None

            except Exception as e:
                logger.warning(f"[playwright] CDN URL {i} download error: {e}")
                if output_path.exists():
                    output_path.unlink()

        return None, None, "playwright: all intercepted CDN URLs failed to download"
