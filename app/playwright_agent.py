"""
Playwright + Gemini YouTube Download Agent — Full Agentic Redesign

Architecture:
  BeautifulSoup parses HTML → PageChunks (typed, searchable)
  Qdrant stores vectorized chunks per-job — the agent's "eyes"
  PostgreSQL tracks page visits and download attempts (playwright_page_visits,
    playwright_download_attempts tables)
  LangGraph StateGraph manages agent state across 14 Playwright tools
  Gemini 2.0 Flash reasons about Qdrant search results to decide next action

Three-tier fallback in download():
  Tier 1: LangGraph + Gemini StateGraph (recursion_limit=30, 120s timeout)
  Tier 2: Hardcoded consent selectors + JS playVideo() (no LLM)
  Tier 3: ytInitialPlayerResponse JS extraction (extract embedded stream URLs)

Strategy 8b in downloader.py — dispatched as kind == "playwright_gemini"
downloader.py imports: YouTubePlaywrightAgent, PLAYWRIGHT_AVAILABLE, LANGCHAIN_GEMINI_AVAILABLE

Why LangGraph StateGraph over create_react_agent:
  - System prompt is rebuilt every step from fresh _AgentSession state
    (current CDN count, current URL, page title) — agent always knows exact status
  - Full ScraperState (message history) persists across all 30 tool calls
  - recursion_limit=30 handles complex multi-step consent flows

Why ctx.request for CDN download:
  - page.on("response") captures signed CDN URLs that are IP-locked (url contains ip=<ip>)
  - ctx.request.get() uses the SAME browser context → same outbound IP → no 403
"""

import asyncio
import base64
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, List, Optional, TypedDict

logger = logging.getLogger(__name__)

# ── Availability flags ───────────────────────────────────────────────────────

PLAYWRIGHT_AVAILABLE = False
LANGCHAIN_GEMINI_AVAILABLE = False
BS4_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
    logger.info("✅ playwright available (Playwright CDN interception strategy active)")
except ImportError:
    logger.warning(
        "⚠️ playwright not installed — run: pip install playwright && playwright install chromium"
    )

try:
    from bs4 import BeautifulSoup  # noqa: F401
    BS4_AVAILABLE = True
except ImportError:
    pass

try:
    from langgraph.graph import StateGraph, END
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
    from langchain_core.tools import tool
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
    LANGCHAIN_GEMINI_AVAILABLE = True
    logger.info("✅ langchain-google-genai + LangGraph (StateGraph) available")
except Exception as e:
    logger.warning(f"⚠️ langchain-google-genai/LangGraph unavailable: {e}")

# ── PageVectorizer / PageTracker (graceful import) ───────────────────────────

VECTORIZER_AVAILABLE = False
TRACKER_AVAILABLE = False

try:
    from .page_vectorizer import PageVectorizer
    VECTORIZER_AVAILABLE = True
except Exception as e:
    logger.warning(f"⚠️ page_vectorizer unavailable: {e}")

try:
    from .page_tracker import PageTracker
    TRACKER_AVAILABLE = True
except Exception as e:
    logger.warning(f"⚠️ page_tracker unavailable: {e}")


# ── LangGraph State ───────────────────────────────────────────────────────────

if LANGCHAIN_GEMINI_AVAILABLE:
    class ScraperState(TypedDict):
        """Agent state flowing through all LangGraph nodes. Messages accumulate via add_messages."""
        messages: Annotated[List[BaseMessage], add_messages]
        job_id: str
        video_url: str
else:
    # Stub for type checkers when LangGraph is unavailable
    ScraperState = dict  # type: ignore[misc,assignment]


# ── Session (mutable per-download browser state) ──────────────────────────────

class _AgentSession:
    """
    Holds live Playwright objects and service clients for one download run.
    LangGraph state holds message history; session holds mutable browser data.
    agent_node rebuilds the system prompt from session each step → agent always
    has accurate current state (CDN count, page title, etc.).
    """

    def __init__(
        self,
        page: Any,
        ctx: Any,
        loop: asyncio.AbstractEventLoop,
        intercepted_cdns: List[str],
        output_path: Path,
        job_id: str,
        video_url: str,
        vectorizer: Optional[Any] = None,
        tracker: Optional[Any] = None,
    ) -> None:
        self.page = page
        self.ctx = ctx
        self.loop = loop
        self.intercepted_cdns = intercepted_cdns  # shared reference with YouTubePlaywrightAgent
        self.page_html: str = ""
        self.page_title: str = ""
        self.current_url: str = ""
        self.visited_urls: List[str] = []
        self.qdrant_collection: Optional[str] = None
        self.output_path = output_path
        self.job_id = job_id
        self.video_url = video_url
        self.vectorizer = vectorizer
        self.tracker = tracker


# ── Tool builder ──────────────────────────────────────────────────────────────

def _build_tools(session: _AgentSession) -> List[Any]:
    """
    Build all 14 agent tools as closures over _AgentSession.

    Async/sync bridge: the LangGraph graph runs in a thread (via run_in_executor).
    Tools are sync closures that schedule async Playwright coroutines on the main
    event loop via asyncio.run_coroutine_threadsafe(coro, session.loop).
    """

    def _run_async(coro: Any, timeout: float = 120.0) -> Any:
        """Schedule async coroutine on session.loop, block until done or timeout."""
        fut = asyncio.run_coroutine_threadsafe(coro, session.loop)
        return fut.result(timeout=timeout)

    # ── Group A: Navigation & Page State ──────────────────────────────────────

    @tool
    def navigate_to(url: str) -> str:
        """Navigate the browser to a URL and vectorize the page content into Qdrant.

        Loads the URL in Chromium, waits 3 seconds for JS to render, extracts
        full HTML, parses it with BeautifulSoup into typed chunks, and stores
        them in Qdrant for subsequent search_page_content() calls.

        Args:
            url: Full URL to navigate to, e.g. 'https://www.youtube.com/watch?v=...'
        """
        async def _go() -> str:
            try:
                await session.page.goto(url, wait_until="commit", timeout=90_000)
                await asyncio.sleep(3)
                html = await session.page.content()
                title = await session.page.title()
                current_url = session.page.url

                session.page_html = html
                session.page_title = title
                session.current_url = current_url
                if current_url not in session.visited_urls:
                    session.visited_urls.append(current_url)

                chunks_count = 0
                if session.vectorizer:
                    collection = await session.vectorizer.vectorize_and_store(
                        html, current_url, title, session.job_id
                    )
                    session.qdrant_collection = collection
                    chunks_count = max(1, len(html) // 500)  # approximate

                if session.tracker:
                    await session.tracker.track_page_visit(
                        session.job_id, session.video_url, current_url, title,
                        chunks_count, session.qdrant_collection or "",
                    )

                return (
                    f"Navigated to: {current_url}\n"
                    f"Title: {title}\n"
                    f"CDN URLs captured so far: {len(session.intercepted_cdns)}\n"
                    f"Page vectorized: {'yes → ' + (session.qdrant_collection or '') if session.qdrant_collection else 'no'}"
                )
            except Exception as e:
                return f"Navigation error: {e}"

        try:
            return _run_async(_go())
        except Exception as e:
            return f"navigate_to failed: {e}"

    @tool
    def refresh_page_context() -> str:
        """Re-capture and re-vectorize the current page after interactions.

        Call this after clicking consent buttons or triggering playback so the
        agent sees the updated DOM. Re-adds new chunks to the Qdrant collection.
        """
        async def _refresh() -> str:
            try:
                html = await session.page.content()
                title = await session.page.title()
                current_url = session.page.url

                session.page_html = html
                session.page_title = title
                session.current_url = current_url

                if session.vectorizer and session.qdrant_collection:
                    await session.vectorizer.vectorize_and_store(
                        html, current_url, title, session.job_id
                    )

                return (
                    f"Page context refreshed.\n"
                    f"URL: {current_url}\n"
                    f"Title: {title}\n"
                    f"CDN URLs captured: {len(session.intercepted_cdns)}"
                )
            except Exception as e:
                return f"refresh_page_context error: {e}"

        try:
            return _run_async(_refresh(), timeout=30.0)
        except Exception as e:
            return f"refresh_page_context failed: {e}"

    @tool
    def get_current_page_info() -> str:
        """Get current browser page URL, title, CDN capture count, and visit history."""
        return (
            f"Current URL: {session.current_url or 'not yet navigated'}\n"
            f"Page title: {session.page_title or 'unknown'}\n"
            f"CDN URLs captured: {len(session.intercepted_cdns)}\n"
            f"Pages visited: {len(session.visited_urls)}\n"
            f"Qdrant collection: {session.qdrant_collection or 'not yet created'}"
        )

    # ── Group B: Page Content Search ──────────────────────────────────────────

    @tool
    def search_page_content(query: str) -> str:
        """Semantically search the vectorized page for elements matching a description.

        Uses Qdrant vector search to find relevant page elements. Returns elements
        with their CSS selectors that can be passed directly to click_element().

        Args:
            query: Natural language description, e.g. 'cookie consent accept button'
                   or 'video player' or 'sign in button' or 'decline cookies'
        """
        if not session.vectorizer or not session.qdrant_collection:
            return (
                "Qdrant vector search unavailable (no collection yet). "
                "Call refresh_page_context() first, or use find_elements_by_text()."
            )

        async def _search() -> str:
            chunks = await session.vectorizer.search(
                session.qdrant_collection, query, limit=8
            )
            if not chunks:
                return f"No matching elements found for: {query!r}"
            lines = [
                f"[{c.element_type.upper()}] {c.content}\n  → selector='{c.selector}'"
                for c in chunks
            ]
            return f"Found {len(lines)} elements for {query!r}:\n\n" + "\n\n".join(lines)

        try:
            return _run_async(_search(), timeout=30.0)
        except Exception as e:
            return f"search_page_content error: {e}"

    @tool
    def find_elements_by_text(text: str) -> str:
        """Find page elements containing specific visible text using BeautifulSoup.

        Searches the current page HTML directly for elements containing the given text.
        Faster than search_page_content for exact text matches.

        Args:
            text: Visible text to search for, e.g. 'Accept all' or 'I agree' or 'Sign in'
        """
        if not BS4_AVAILABLE or not session.page_html:
            return "Page HTML not available — call refresh_page_context() first."

        try:
            soup = BeautifulSoup(session.page_html, "html.parser")
            pattern = re.compile(re.escape(text), re.IGNORECASE)
            matches = soup.find_all(string=pattern)
            if not matches:
                return f"No elements found containing text: {text!r}"

            results = []
            for m in matches[:10]:
                parent = m.parent
                if parent and parent.name:
                    el_id = str(parent.get("id", ""))
                    classes = " ".join(parent.get("class", []))[:60]
                    aria = str(parent.get("aria-label", ""))[:60]
                    if el_id:
                        sel = f"#{el_id}"
                    elif aria:
                        sel = f'[aria-label*="{aria}"]'
                    else:
                        sel = parent.name
                    results.append(
                        f"[{parent.name.upper()}] '{str(m)[:80]}'"
                        f"\n  selector='{sel}' class='{classes}'"
                    )
            return "\n\n".join(results)
        except Exception as e:
            return f"find_elements_by_text error: {e}"

    # ── Group C: Playwright Element Interaction ───────────────────────────────

    @tool
    def click_element(selector: str) -> str:
        """Click a DOM element by CSS selector.

        Args:
            selector: Valid CSS selector, e.g. '#accept-button' or
                      'button[aria-label*="Accept"]' or '.consent-accept-btn'
        """
        async def _click() -> str:
            try:
                await session.page.click(selector, timeout=8_000)
                return f"Clicked: {selector}"
            except Exception as e:
                return f"Click failed ({selector}): {e}"

        try:
            return _run_async(_click(), timeout=15.0)
        except Exception as e:
            return f"click_element failed: {e}"

    @tool
    def click_by_visible_text(text: str) -> str:
        """Click the first element with visible text matching the given string.

        Uses Playwright's get_by_text() — more robust than CSS selectors for
        consent buttons whose selectors may vary across regions/languages.

        Args:
            text: Visible button/link text, e.g. 'Accept all' or 'I agree' or 'Reject all'
        """
        async def _click() -> str:
            try:
                await session.page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked element with text: '{text}'"
            except Exception as e:
                return f"click_by_visible_text failed for '{text}': {e}"

        try:
            return _run_async(_click(), timeout=15.0)
        except Exception as e:
            return f"click_by_visible_text failed: {e}"

    @tool
    def click_by_role(role: str, name: str) -> str:
        """Click an element by its ARIA role and accessible name.

        Args:
            role: ARIA role, e.g. 'button', 'link', 'checkbox', 'menuitem'
            name: Accessible name or label text, e.g. 'Accept all cookies'
        """
        async def _click() -> str:
            try:
                await session.page.get_by_role(role, name=name).first.click(timeout=8_000)
                return f"Clicked role={role!r} name={name!r}"
            except Exception as e:
                return f"click_by_role failed (role={role!r}, name={name!r}): {e}"

        try:
            return _run_async(_click(), timeout=15.0)
        except Exception as e:
            return f"click_by_role failed: {e}"

    @tool
    def fill_form_field(selector: str, value: str) -> str:
        """Fill a form input field with a value.

        Args:
            selector: CSS selector for the input field
            value: Value to type into the field
        """
        async def _fill() -> str:
            try:
                await session.page.fill(selector, value, timeout=8_000)
                return f"Filled {selector!r} with value"
            except Exception as e:
                return f"fill_form_field failed ({selector}): {e}"

        try:
            return _run_async(_fill(), timeout=15.0)
        except Exception as e:
            return f"fill_form_field failed: {e}"

    @tool
    def execute_javascript(code: str) -> str:
        """Execute a JavaScript expression in the browser and return the result.

        Args:
            code: JavaScript expression to evaluate. Example to trigger playback:
                  "(function(){var p=document.getElementById('movie_player');if(p&&p.playVideo){p.playVideo();return 'yt-api';}var v=document.querySelector('video');if(v){v.play();return 'html5';}return 'noplayer';})()"
        """
        async def _eval() -> str:
            try:
                result = await session.page.evaluate(code)
                return f"JS result: {result}"
            except Exception as e:
                return f"JS error: {e}"

        try:
            return _run_async(_eval(), timeout=20.0)
        except Exception as e:
            return f"execute_javascript failed: {e}"

    # ── Group D: Wait & Verify ─────────────────────────────────────────────────

    @tool
    def wait_for_element(selector: str, timeout_ms: int = 10000) -> str:
        """Wait for a CSS selector to appear on the page.

        Args:
            selector: CSS selector to wait for, e.g. 'video' or '#movie_player' or '.ytp-play-button'
            timeout_ms: Maximum wait time in milliseconds (default 10000)
        """
        async def _wait() -> str:
            try:
                await session.page.wait_for_selector(selector, timeout=timeout_ms)
                return f"Element found: {selector}"
            except Exception as e:
                return f"wait_for_element timed out or failed ({selector}): {e}"

        try:
            return _run_async(_wait(), timeout=(timeout_ms / 1000) + 5)
        except Exception as e:
            return f"wait_for_element failed: {e}"

    @tool
    def check_captured_video_urls() -> str:
        """Check how many YouTube CDN video URLs the network interceptor has captured.

        CDN URLs appear after video playback starts. They may take 5-10 seconds.
        Call this repeatedly after triggering playback until URLs appear.
        Once you have CDN URLs, call download_video_url() with the first one.
        """
        if not session.intercepted_cdns:
            return (
                "No CDN URLs captured yet — playback may not have started.\n"
                "Try execute_javascript to trigger playback, wait a few seconds, then retry."
            )
        shown = session.intercepted_cdns[:5]
        lines = [f"  {i + 1}. {u[:100]}..." for i, u in enumerate(shown)]
        return (
            f"Captured {len(session.intercepted_cdns)} CDN URLs:\n"
            + "\n".join(lines)
            + "\n\nCall download_video_url with URL #1 to download."
        )

    # ── Group E: Download & Screenshot ────────────────────────────────────────

    @tool
    def download_video_url(cdn_url: str) -> str:
        """Download a YouTube CDN video URL using the browser's request context.

        IMPORTANT: Uses ctx.request (same browser IP as when YouTube signed the URL).
        This avoids the 403 that occurs when downloading from a different IP.
        Only pass URLs from check_captured_video_urls().

        Args:
            cdn_url: Full googlevideo.com CDN URL from check_captured_video_urls()
        """
        async def _download() -> str:
            try:
                api_resp = await session.ctx.request.get(
                    cdn_url,
                    headers={
                        "Referer": "https://www.youtube.com/",
                        "Origin": "https://www.youtube.com",
                    },
                    timeout=300_000,  # 5 minutes
                )
                if not api_resp.ok:
                    error_msg = f"CDN returned HTTP {api_resp.status}"
                    if session.tracker:
                        await session.tracker.track_download_attempt(
                            session.job_id, cdn_url, False, error_msg, None
                        )
                    return error_msg

                body = await api_resp.body()
                if not body:
                    error_msg = "CDN returned empty body"
                    if session.tracker:
                        await session.tracker.track_download_attempt(
                            session.job_id, cdn_url, False, error_msg, None
                        )
                    return error_msg

                session.output_path.write_bytes(body)
                size_mb = len(body) / 1024 / 1024
                if session.tracker:
                    await session.tracker.track_download_attempt(
                        session.job_id, cdn_url, True, None, len(body)
                    )
                return f"Downloaded {size_mb:.1f} MB → {session.output_path.name}"

            except Exception as e:
                error_msg = str(e)
                if session.tracker:
                    try:
                        await session.tracker.track_download_attempt(
                            session.job_id, cdn_url, False, error_msg, None
                        )
                    except Exception:
                        pass
                return f"Download error: {error_msg}"

        try:
            return _run_async(_download(), timeout=330.0)
        except Exception as e:
            return f"download_video_url failed: {e}"

    @tool
    def take_screenshot() -> str:
        """Take a screenshot of the current browser state for debugging.

        Useful when the agent needs to understand what is currently shown on screen.
        Returns page URL, title, and screenshot metadata (not the image data itself).
        """
        async def _screenshot() -> str:
            try:
                raw = await session.page.screenshot(type="png", full_page=False)
                title = await session.page.title()
                url = session.page.url
                b64_prefix = base64.b64encode(raw[:30]).decode()
                return (
                    f"Screenshot taken ({len(raw)} bytes)\n"
                    f"Current URL: {url}\n"
                    f"Page title: {title}\n"
                    f"(Base64 prefix: {b64_prefix}...)"
                )
            except Exception as e:
                return f"Screenshot error: {e}"

        try:
            return _run_async(_screenshot(), timeout=20.0)
        except Exception as e:
            return f"take_screenshot failed: {e}"

    return [
        navigate_to, refresh_page_context, get_current_page_info,
        search_page_content, find_elements_by_text,
        click_element, click_by_visible_text, click_by_role, fill_form_field, execute_javascript,
        wait_for_element, check_captured_video_urls,
        download_video_url, take_screenshot,
    ]


# ── LangGraph Graph Builder ───────────────────────────────────────────────────

def _build_agent_graph(session: _AgentSession, llm: Any) -> Any:
    """
    Build and compile the LangGraph StateGraph for the scraper agent.

    StateGraph (not create_react_agent) because:
    - agent_node is a closure over session → system prompt rebuilt each step
      with fresh CDN count, page URL, title
    - Full message history accumulates via add_messages reducer
    - recursion_limit=30 handles complex multi-step consent flows
    """
    tools = _build_tools(session)
    tool_node = ToolNode(tools)
    llm_with_tools = llm.bind_tools(tools)

    def agent_node(state: ScraperState) -> dict:
        """Gemini reasons about current browser state and decides next action."""
        system_content = (
            f"You are an agentic web scraper controlling a Chrome browser to download a YouTube video.\n\n"
            f"Target video: {state['video_url']}\n"
            f"Current page URL: {session.current_url or state['video_url']}\n"
            f"Page title: {session.page_title or 'not yet loaded'}\n"
            f"CDN URLs captured: {len(session.intercepted_cdns)}\n"
            f"Pages visited: {len(session.visited_urls)}\n"
            f"Qdrant collection: {session.qdrant_collection or 'not yet created'}\n\n"
            f"GOAL: Download the video file to disk.\n\n"
            f"STRATEGY:\n"
            f"1. Call refresh_page_context() to capture the current page into Qdrant\n"
            f"2. Call search_page_content('cookie consent accept button dialog') to find consent UI\n"
            f"3. If consent dialog found:\n"
            f"   - Try click_by_visible_text('Accept all') first\n"
            f"   - Or use click_element(selector) with the selector from search results\n"
            f"   - Then call refresh_page_context() to see the updated page\n"
            f"4. Trigger video playback with execute_javascript:\n"
            f"   \"(function(){{var p=document.getElementById('movie_player');"
            f"if(p&&p.playVideo){{p.playVideo();return 'yt-api';}}"
            f"var v=document.querySelector('video');if(v){{v.play();return 'html5';}}"
            f"return 'noplayer';}})()\"\n"
            f"5. Call check_captured_video_urls() — CDN URLs appear 5-10s after playback starts\n"
            f"   Repeat check if no URLs yet (wait implies playback still initialising)\n"
            f"6. Once CDN URLs are available: call download_video_url(cdn_url) with URL #1\n\n"
            f"IMPORTANT:\n"
            f"- Stop immediately after download_video_url returns 'Downloaded X MB'\n"
            f"- Do NOT navigate away from the YouTube video page\n"
            f"- Use search_page_content() results' selectors directly in click_element()\n"
            f"- CDN URLs are IP-locked: download_video_url must be used (not navigate_to)\n"
        )
        system_msg = SystemMessage(content=system_content)
        messages = [system_msg] + list(state["messages"])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: ScraperState) -> str:
        """Route to tools node if agent made tool calls, otherwise end the graph."""
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    workflow = StateGraph(ScraperState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", END: END}
    )
    workflow.add_edge("tools", "agent")
    return workflow.compile()


# ── Main Agent Class ───────────────────────────────────────────────────────────

class YouTubePlaywrightAgent:
    """
    Playwright + Gemini browser agent for YouTube CDN URL interception and download.
    Instantiate fresh per download call (holds mutable per-session state).
    """

    def __init__(self) -> None:
        self.intercepted_cdns: List[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._llm: Optional[Any] = None
        self._embeddings: Optional[Any] = None

        api_key = os.getenv("GOOGLE_API_KEY")
        if LANGCHAIN_GEMINI_AVAILABLE and api_key:
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-3.1-pro-preview",
                google_api_key=api_key,
                temperature=1,
            )
            self._embeddings = GoogleGenerativeAIEmbeddings(
                model="models/gemini-embedding-001",
                google_api_key=api_key,
            )
            logger.info("✅ gemini-3.1-pro-preview + gemini-embedding-001 initialized")

    # ── CDN interceptor (sync handler — fires for every network response) ──────

    def _intercept_response(self, response: Any) -> None:
        """
        Synchronous Playwright event handler — fires for EVERY network response.
        Registered via page.on("response", ...) BEFORE page.goto() so we never
        miss early CDN requests YouTube's player makes during page load.
        Sync handler is safe: only appends to a list, no I/O.
        """
        url = response.url
        if "googlevideo.com/videoplayback" not in url:
            return
        if url in self.intercepted_cdns:
            return
        # Skip SABR (Server-side ABR) protocol URLs — require binary request body
        if "sabr=1" in url:
            return
        # Video streams prepended (higher priority); audio-only appended
        if "mime=video" in url or "mime%3Dvideo" in url:
            self.intercepted_cdns.insert(0, url)
        else:
            self.intercepted_cdns.append(url)
        logger.info(f"[playwright] CDN captured #{len(self.intercepted_cdns)}: {url[:80]}...")

    # ── Tier 1: LangGraph + Gemini StateGraph ─────────────────────────────────

    async def _run_tier1_agent(self, session: _AgentSession) -> None:
        """
        Run the full LangGraph StateGraph agent with Gemini reasoning.
        Runs in an executor (sync thread) — tools bridge back via run_coroutine_threadsafe.
        """
        if not LANGCHAIN_GEMINI_AVAILABLE or not self._llm:
            return

        graph = _build_agent_graph(session, self._llm)

        initial_state: dict = {
            "messages": [
                HumanMessage(content=(
                    f"The browser has already loaded the YouTube page at {session.video_url}. "
                    f"Please download the video file."
                ))
            ],
            "job_id": session.job_id,
            "video_url": session.video_url,
        }

        def _run_sync() -> None:
            try:
                graph.invoke(initial_state, config={"recursion_limit": 30})
            except Exception as e:
                logger.warning(f"[playwright] LangGraph agent error: {e}")

        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _run_sync),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[playwright] Tier 1 agent timed out after 120s")

    # ── Tier 2: Simple hardcoded interaction (no LLM) ─────────────────────────

    async def _run_tier2_simple(self, page: Any) -> None:
        """
        Try hardcoded consent selectors + JS playback trigger.
        Runs when Gemini is unavailable or Tier 1 produced no download.
        """
        consent_selectors = [
            "button[aria-label*='Accept']",
            "button[aria-label*='Agree']",
            "#introAgreeButton",
            "form[action*='consent'] button",
            ".eom-button-row button:first-child",
            "tp-yt-paper-button#agree-button",
            "ytd-consent-bump-v2-renderer button",
            "[data-testid='uc-accept-all-button']",
        ]
        for sel in consent_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    logger.info(f"[playwright] Tier 2: dismissed consent via {sel}")
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass

        try:
            result = await page.evaluate("""
                (function() {
                    var p = document.getElementById('movie_player');
                    if (p && p.playVideo) { p.playVideo(); return 'yt-api'; }
                    var v = document.querySelector('video');
                    if (v) { v.play(); return 'html5'; }
                    return 'no-player';
                })();
            """)
            logger.info(f"[playwright] Tier 2: JS playback result: {result}")
        except Exception as e:
            logger.warning(f"[playwright] Tier 2: JS playback error: {e}")

    # ── Tier 3: ytInitialPlayerResponse JS extraction ─────────────────────────

    async def _run_tier3_extract(
        self, page: Any, ctx: Any, output_path: Path
    ) -> Optional[str]:
        """
        Extract video stream URLs from the ytInitialPlayerResponse JS variable
        embedded in the page, then download via ctx.request (same IP, no 403).
        Returns None on success, error string on failure.
        """
        try:
            formats = await page.evaluate(
                "window.ytInitialPlayerResponse?.streamingData?.formats || []"
            )
            adaptive = await page.evaluate(
                "window.ytInitialPlayerResponse?.streamingData?.adaptiveFormats || []"
            )
            all_formats = list(formats or []) + list(adaptive or [])

            if not all_formats:
                logger.warning("[playwright] Tier 3: no formats in ytInitialPlayerResponse")
                return "Tier 3: no formats in ytInitialPlayerResponse"

            logger.info(f"[playwright] Tier 3: found {len(all_formats)} formats")

            # Prefer formats with direct URL (not cipher-encrypted)
            urls_to_try = [
                f["url"] for f in all_formats
                if f.get("url") and "googlevideo.com" in str(f.get("url", ""))
            ][:5]

            if not urls_to_try:
                return "Tier 3: no direct URL formats found (all cipher-encrypted)"

            for i, url in enumerate(urls_to_try, 1):
                try:
                    logger.info(f"[playwright] Tier 3: trying format {i}/{len(urls_to_try)}")
                    api_resp = await ctx.request.get(
                        url,
                        headers={"Referer": "https://www.youtube.com/"},
                        timeout=300_000,
                    )
                    if not api_resp.ok:
                        logger.warning(f"[playwright] Tier 3 format {i}: HTTP {api_resp.status}")
                        continue
                    body = await api_resp.body()
                    if not body:
                        continue
                    output_path.write_bytes(body)
                    size_mb = len(body) / 1024 / 1024
                    logger.info(f"[playwright] Tier 3: ✅ Downloaded {size_mb:.1f} MB")
                    return None  # success
                except Exception as e:
                    logger.warning(f"[playwright] Tier 3 format {i} error: {e}")

            return "Tier 3: all ytInitialPlayerResponse format URLs failed to download"

        except Exception as e:
            return f"Tier 3 extraction error: {e}"

    # ── Main download entrypoint ───────────────────────────────────────────────

    async def download(
        self,
        video_url: str,
        job_dir: Path,
        quality: str,
    ) -> tuple:
        """
        Full agentic download flow with three-tier fallback.
        Returns (file_path, VideoMetadata, None) on success.
        Returns (None, None, error_string) on failure.
        """
        from .models import VideoMetadata

        if not PLAYWRIGHT_AVAILABLE:
            return None, None, "playwright not installed"

        job_id = str(uuid.uuid4())[:16]
        output_path = job_dir / "video.mp4"
        self.intercepted_cdns = []
        video_title = "Unknown"
        download_error: Optional[str] = "playwright: no CDN URLs intercepted after playback"

        # Build service clients
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        database_url = os.getenv("DATABASE_URL")

        vectorizer: Optional[Any] = None
        if VECTORIZER_AVAILABLE and self._embeddings:
            vectorizer = PageVectorizer(
                qdrant_url=qdrant_url,
                qdrant_api_key=qdrant_api_key,
                embeddings_model=self._embeddings,
            )

        tracker: Optional[Any] = None
        if TRACKER_AVAILABLE:
            tracker = PageTracker(database_url=database_url)
            await tracker.ensure_tables()

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
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
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )
                page = await ctx.new_page()
                self._loop = asyncio.get_event_loop()

                # CRITICAL: register BEFORE goto() — never miss early CDN requests
                page.on("response", self._intercept_response)

                logger.info(f"[playwright] Navigating to {video_url}")
                await page.goto(video_url, wait_until="commit", timeout=90_000)
                # "commit" fires when response headers arrive — earlier than domcontentloaded.
                # Give YouTube's JS player time to initialize and issue CDN requests.
                await asyncio.sleep(5)

                # Capture initial page state
                html = await page.content()
                title = await page.title()
                current_url = page.url
                video_title = title

                # Build session (intercepted_cdns is shared reference)
                session = _AgentSession(
                    page=page,
                    ctx=ctx,
                    loop=self._loop,
                    intercepted_cdns=self.intercepted_cdns,
                    output_path=output_path,
                    job_id=job_id,
                    video_url=video_url,
                    vectorizer=vectorizer,
                    tracker=tracker,
                )
                session.page_html = html
                session.page_title = title
                session.current_url = current_url
                session.visited_urls = [current_url]

                # Pre-vectorize initial page — agent can search immediately
                if vectorizer and self._embeddings:
                    collection = await vectorizer.vectorize_and_store(
                        html, current_url, title, job_id
                    )
                    session.qdrant_collection = collection
                    logger.info(f"[playwright] Initial page vectorized → {collection}")

                if tracker:
                    await tracker.track_page_visit(
                        job_id, video_url, current_url, title, 0,
                        session.qdrant_collection or "",
                    )

                # ── TIER 1: LangGraph + Gemini StateGraph ─────────────────────
                logger.info("[playwright] Starting Tier 1: LangGraph + Gemini StateGraph agent")
                if LANGCHAIN_GEMINI_AVAILABLE and self._llm:
                    await self._run_tier1_agent(session)
                else:
                    logger.info("[playwright] Tier 1 skipped — Gemini API key not configured")

                # Check Tier 1 outcome
                if output_path.exists() and output_path.stat().st_size > 0:
                    size_mb = output_path.stat().st_size / 1024 / 1024
                    logger.info(f"[playwright] ✅ Tier 1 succeeded: {size_mb:.1f} MB")
                    download_error = None
                else:
                    # ── TIER 2: Simple hardcoded interaction ──────────────────
                    logger.info(
                        "[playwright] Tier 1 produced no download — "
                        "Tier 2: hardcoded consent selectors + JS playback"
                    )
                    await self._run_tier2_simple(page)
                    logger.info("[playwright] Waiting 8s for CDN stream responses...")
                    await asyncio.sleep(8)

                    # Try CDN URLs captured after Tier 2 interaction
                    if self.intercepted_cdns:
                        download_error = "playwright: all intercepted CDN URLs failed"
                        for i, cdn_url in enumerate(self.intercepted_cdns[:5], 1):
                            try:
                                logger.info(
                                    f"[playwright] Tier 2: CDN URL "
                                    f"{i}/{min(5, len(self.intercepted_cdns))}"
                                )
                                api_resp = await ctx.request.get(
                                    cdn_url,
                                    headers={
                                        "Referer": "https://www.youtube.com/",
                                        "Origin": "https://www.youtube.com",
                                    },
                                    timeout=300_000,
                                )
                                if not api_resp.ok:
                                    logger.warning(
                                        f"[playwright] Tier 2 CDN {i}: HTTP {api_resp.status}"
                                    )
                                    continue
                                body = await api_resp.body()
                                if not body:
                                    continue
                                output_path.write_bytes(body)
                                size_mb = output_path.stat().st_size / 1024 / 1024
                                logger.info(f"[playwright] ✅ Tier 2 succeeded: {size_mb:.1f} MB")
                                download_error = None
                                break
                            except Exception as e:
                                logger.warning(f"[playwright] Tier 2 CDN {i} error: {e}")
                                if output_path.exists():
                                    output_path.unlink()

                    # ── TIER 3: ytInitialPlayerResponse extraction ─────────────
                    if not (output_path.exists() and output_path.stat().st_size > 0):
                        logger.info(
                            "[playwright] Tier 2 failed — "
                            "Tier 3: ytInitialPlayerResponse extraction"
                        )
                        tier3_error = await self._run_tier3_extract(page, ctx, output_path)
                        if tier3_error is None:
                            download_error = None
                            logger.info("[playwright] ✅ Tier 3 succeeded")
                        else:
                            download_error = tier3_error
                            logger.warning(f"[playwright] Tier 3 failed: {tier3_error}")

                # ── Cleanup ────────────────────────────────────────────────────
                if vectorizer and session.qdrant_collection:
                    try:
                        await vectorizer.drop_collection(session.qdrant_collection)
                    except Exception:
                        pass

                if tracker:
                    try:
                        await tracker.close()
                    except Exception:
                        pass

                video_title = session.page_title or title
                await browser.close()

        except asyncio.TimeoutError:
            return None, None, "playwright: browser session timed out"
        except Exception as e:
            return None, None, f"playwright browser error: {e}"

        if download_error is not None:
            return None, None, download_error

        if not output_path.exists() or output_path.stat().st_size == 0:
            return None, None, "playwright: output file empty or missing"

        metadata = VideoMetadata(
            title=video_title.replace(" - YouTube", "").strip() or "Unknown",
            duration_seconds=0.0,
            file_size_bytes=output_path.stat().st_size,
            format="mp4",
            is_live=False,
            is_private=False,
        )
        return output_path, metadata, None
