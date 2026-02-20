"""
PageVectorizer — BeautifulSoup HTML parsing into typed, searchable PageChunks stored in Qdrant.

Why Qdrant over FAISS:
  - Qdrant persists across agent steps (not re-built on every page capture)
  - Per-job collection isolates pages across concurrent downloads
  - Semantic search returns CSS selectors the agent can directly pass to Playwright tools
  - FAISS fallback preserved for environments without Qdrant (in-memory only)

Element categories extracted:
  1.  Buttons       <button> → text, id, aria-label, class, data-testid
  2.  Links         <a href> → text, href, aria-label
  3.  Inputs        <input>, <select>, <textarea> → type, name, placeholder, label
  4.  Forms         grouped input descriptions + action URL
  5.  Videos        <video>, <iframe[src*=youtube]> → src, id, class
  6.  Headings      <h1>–<h4> → text (page structure)
  7.  Body text     <p>, <span>, <div> with > 40 chars visible text
  8.  Dialogs       role=dialog, class*=modal/consent/overlay/cookie
  9.  Navigation    <nav> → all child <a> tags with href + text
  10. YT components ytd-* Web Components, #movie_player
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ── Availability flags ───────────────────────────────────────────────────────

QDRANT_AVAILABLE = False
BS4_AVAILABLE = False

try:
    from bs4 import BeautifulSoup, Tag  # noqa: F401
    BS4_AVAILABLE = True
    logger.info("✅ beautifulsoup4 available (full HTML parsing enabled)")
except ImportError:
    logger.warning("⚠️ beautifulsoup4 not installed — HTML parsing disabled")

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
    QDRANT_AVAILABLE = True
    logger.info("✅ qdrant-client available (Qdrant vector storage enabled)")
except ImportError:
    logger.warning("⚠️ qdrant-client not installed — falling back to FAISS in-memory")


# ── PageChunk dataclass ───────────────────────────────────────────────────────

@dataclass
class PageChunk:
    """A semantic chunk of a web page element, ready for vector storage and retrieval."""
    content: str          # Human-readable description: "BUTTON: text='Accept all' id='btn-accept'"
    element_type: str     # "button"|"link"|"input"|"form"|"video"|"heading"|"text"|"dialog"|"nav"|"yt_component"
    selector: str         # Best CSS selector for Playwright: '#id' or '[aria-label*="..."]' etc.
    text: str             # Visible text content of the element
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ── PageVectorizer ────────────────────────────────────────────────────────────

class PageVectorizer:
    """
    Parses HTML into typed PageChunks using BeautifulSoup, embeds with Gemini,
    and stores in a per-job Qdrant collection for semantic retrieval.

    Collection naming: playwright_page_{job_id[:16]}
    Fallback: FAISS in-memory when Qdrant is unavailable.
    """

    COLLECTION_PREFIX = "playwright_page_"
    VECTOR_DIM = 3072  # gemini-embedding-001 actual default output dimension

    def __init__(
        self,
        qdrant_url: Optional[str],
        qdrant_api_key: Optional[str],
        embeddings_model: Optional[Any],
    ) -> None:
        self._embeddings = embeddings_model
        self._qdrant: Optional[Any] = None
        self._faiss_store: Optional[Any] = None

        if QDRANT_AVAILABLE and qdrant_url:
            try:
                self._qdrant = QdrantClient(
                    url=qdrant_url,
                    api_key=qdrant_api_key,
                    timeout=30,
                    check_compatibility=False,
                )
                logger.info(f"✅ PageVectorizer: Qdrant connected at {qdrant_url[:50]}")
            except Exception as e:
                logger.warning(f"⚠️ PageVectorizer: Qdrant connection failed: {e} — using FAISS")

    # ── Public API ────────────────────────────────────────────────────────────

    async def vectorize_and_store(
        self,
        html: str,
        url: str,
        title: str,
        job_id: str,
    ) -> str:
        """
        Parse HTML → extract PageChunks → embed → upsert to Qdrant.
        Returns collection_name for subsequent search() calls.
        """
        import asyncio
        collection_name = f"{self.COLLECTION_PREFIX}{job_id[:16]}"

        if not BS4_AVAILABLE or not self._embeddings:
            return collection_name

        chunks = await asyncio.get_event_loop().run_in_executor(
            None, self._extract_chunks_from_html, html, url
        )

        if not chunks:
            logger.warning(f"[vectorizer] No chunks extracted from {url[:60]}")
            return collection_name

        logger.info(f"[vectorizer] Extracted {len(chunks)} chunks from {url[:60]}")

        if self._qdrant:
            await self._upsert_to_qdrant(chunks, collection_name, url, title)
        else:
            await self._build_faiss(chunks, url, title)

        return collection_name

    async def search(
        self,
        collection_name: str,
        query: str,
        limit: int = 8,
    ) -> List[PageChunk]:
        """
        Semantic search over vectorized page content.
        Returns ranked PageChunks with CSS selectors ready for Playwright tools.
        """
        import asyncio

        if not self._embeddings:
            return []

        try:
            query_vector = await asyncio.get_event_loop().run_in_executor(
                None, self._embeddings.embed_query, query
            )
        except Exception as e:
            logger.warning(f"[vectorizer] Query embedding error: {e}")
            return []

        # Normalize query vector dimension to match stored vectors
        if len(query_vector) != self.VECTOR_DIM:
            if len(query_vector) < self.VECTOR_DIM:
                query_vector = query_vector + [0.0] * (self.VECTOR_DIM - len(query_vector))
            else:
                query_vector = query_vector[:self.VECTOR_DIM]

        # Qdrant search (qdrant-client >= 1.10 uses query_points; search was removed in 1.10+)
        if self._qdrant:
            try:
                response = self._qdrant.query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    limit=limit,
                    with_payload=True,
                )
                return [
                    PageChunk(
                        content=r.payload.get("content", ""),
                        element_type=r.payload.get("element_type", "unknown"),
                        selector=r.payload.get("selector", ""),
                        text=r.payload.get("text", ""),
                    )
                    for r in response.points
                ]
            except Exception as e:
                logger.warning(f"[vectorizer] Qdrant search error: {e}")

        # FAISS fallback
        if self._faiss_store:
            try:
                results = self._faiss_store.similarity_search_with_score(query, k=limit)
                return [
                    PageChunk(
                        content=d.page_content,
                        element_type=d.metadata.get("element_type", "unknown"),
                        selector=d.metadata.get("selector", ""),
                        text=d.metadata.get("text", ""),
                    )
                    for d, _ in results
                ]
            except Exception as e:
                logger.warning(f"[vectorizer] FAISS search error: {e}")

        return []

    async def drop_collection(self, collection_name: str) -> None:
        """Clean up Qdrant collection after job completes."""
        if self._qdrant:
            try:
                self._qdrant.delete_collection(collection_name)
                logger.info(f"[vectorizer] Dropped Qdrant collection: {collection_name}")
            except Exception as e:
                logger.warning(f"[vectorizer] Failed to drop {collection_name}: {e}")

    # ── HTML parsing ──────────────────────────────────────────────────────────

    def _extract_chunks_from_html(self, html: str, url: str) -> List[PageChunk]:
        """
        Extract 10 element categories from HTML using BeautifulSoup.
        No limit — full page parsed for complete agent visibility.
        """
        if not BS4_AVAILABLE:
            return []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.warning(f"[vectorizer] BS4 parse error: {e}")
            return []

        chunks: List[PageChunk] = []

        # 1. Buttons
        for el in soup.find_all("button"):
            text = el.get_text(strip=True)[:120]
            attrs: dict = {
                k: (v if isinstance(v, str) else " ".join(v))
                for k, v in el.attrs.items()
                if k in ("id", "aria-label", "class", "data-testid", "name", "value")
            }
            if not text and not attrs:
                continue
            parts = [f"text='{text}'"] if text else []
            for k, v in attrs.items():
                if v:
                    parts.append(f"{k}='{str(v)[:60]}'")
            chunks.append(PageChunk(
                content="BUTTON: " + " ".join(parts),
                element_type="button",
                selector=self._element_to_selector(el),
                text=text,
            ))

        # 2. Links
        for el in soup.find_all("a", href=True):
            text = el.get_text(strip=True)[:100]
            href = str(el.get("href", ""))[:150]
            aria = str(el.get("aria-label", ""))[:80]
            if not text and not aria:
                continue
            content = f"LINK: text='{text}' href='{href}'"
            if aria:
                content += f" aria-label='{aria}'"
            chunks.append(PageChunk(
                content=content,
                element_type="link",
                selector=self._element_to_selector(el),
                text=text,
            ))

        # 3. Inputs, selects, textareas
        for el in soup.find_all(["input", "select", "textarea"]):
            el_type = el.get("type", "text") if el.name == "input" else el.name
            name = str(el.get("name", ""))[:60]
            placeholder = str(el.get("placeholder", ""))[:80]
            el_id = str(el.get("id", ""))[:60]
            label_text = ""
            if el_id:
                label = soup.find("label", attrs={"for": el_id})
                if label:
                    label_text = label.get_text(strip=True)[:80]
            content = f"INPUT[{el_type}]: name='{name}' placeholder='{placeholder}'"
            if label_text:
                content += f" label='{label_text}'"
            if el_id:
                content += f" id='{el_id}'"
            chunks.append(PageChunk(
                content=content,
                element_type="input",
                selector=self._element_to_selector(el),
                text=label_text or placeholder or name,
            ))

        # 4. Forms
        for el in soup.find_all("form"):
            action = str(el.get("action", ""))[:100]
            method = str(el.get("method", "GET")).upper()
            input_names = [
                str(i.get("name", "")) for i in el.find_all(["input", "button"])
                if i.get("name") or i.get("type") == "submit"
            ]
            content = f"FORM: action='{action}' method={method}"
            if input_names:
                content += f" fields={input_names[:8]}"
            chunks.append(PageChunk(
                content=content,
                element_type="form",
                selector=self._element_to_selector(el),
                text=action,
            ))

        # 5. Videos and YouTube iframes
        for el in soup.find_all(["video", "iframe"]):
            src = str(el.get("src", ""))[:150]
            if el.name == "iframe" and "youtube" not in src and "googlevideo" not in src:
                continue
            el_id = str(el.get("id", ""))
            cls = " ".join(el.get("class", []))[:80]
            content = f"VIDEO: src='{src[:80]}' id='{el_id}' class='{cls}'"
            chunks.append(PageChunk(
                content=content,
                element_type="video",
                selector=self._element_to_selector(el),
                text=src,
            ))

        # 6. Headings
        for el in soup.find_all(["h1", "h2", "h3", "h4"]):
            text = el.get_text(strip=True)[:150]
            if not text:
                continue
            chunks.append(PageChunk(
                content=f"HEADING[{el.name}]: '{text}'",
                element_type="heading",
                selector=self._element_to_selector(el),
                text=text,
            ))

        # 7. Body text (paragraphs, spans, divs with meaningful content)
        for el in soup.find_all(["p", "span", "div"]):
            if len(list(el.children)) > 5:
                continue
            text = el.get_text(strip=True)[:200]
            if len(text) < 40:
                continue
            chunks.append(PageChunk(
                content=f"TEXT[{el.name}]: '{text[:120]}'",
                element_type="text",
                selector=self._element_to_selector(el),
                text=text[:120],
            ))

        # 8. Dialogs and modals
        dialog_class_patterns = ["modal", "consent", "overlay", "popup", "cookie", "gdpr", "banner"]
        for el in soup.find_all(True, attrs={"role": "dialog"}):
            child_texts = [c.get_text(strip=True)[:60] for c in el.find_all(["button", "a"])[:8]]
            content = f"DIALOG: role=dialog selector='{self._element_to_selector(el)}' contains={child_texts}"
            chunks.append(PageChunk(
                content=content,
                element_type="dialog",
                selector=self._element_to_selector(el),
                text=" ".join(child_texts),
            ))
        for el in soup.find_all(True):
            cls_list = el.get("class", [])
            cls_str = " ".join(cls_list).lower()
            if any(pat in cls_str for pat in dialog_class_patterns):
                child_texts = [c.get_text(strip=True)[:60] for c in el.find_all(["button", "a"])[:8]]
                if not child_texts:
                    continue
                content = f"DIALOG: class='{cls_str[:80]}' contains={child_texts}"
                chunks.append(PageChunk(
                    content=content,
                    element_type="dialog",
                    selector=self._element_to_selector(el),
                    text=" ".join(child_texts),
                ))

        # 9. Navigation
        for nav in soup.find_all("nav"):
            links = []
            for a in nav.find_all("a", href=True)[:10]:
                t = a.get_text(strip=True)[:50]
                h = str(a.get("href", ""))[:80]
                if t:
                    links.append(f"{t}→{h}")
            if not links:
                continue
            chunks.append(PageChunk(
                content=f"NAV: links={links}",
                element_type="nav",
                selector=self._element_to_selector(nav),
                text=" ".join(links),
            ))

        # 10. YouTube-specific Web Components
        yt_components = [
            "ytd-consent-bump-v2-renderer",
            "ytd-enforcement-message-view-model",
            "yt-confirm-dialog-renderer",
            "ytd-popup-container",
        ]
        for comp_name in yt_components:
            for el in soup.find_all(comp_name):
                child_texts = [
                    c.get_text(strip=True)[:60]
                    for c in el.find_all(["button", "a", "yt-button-renderer"])[:10]
                ]
                content = f"YT_COMPONENT: {comp_name} contains={child_texts}"
                if el.get("id"):
                    content += f" id='{el['id']}'"
                chunks.append(PageChunk(
                    content=content,
                    element_type="yt_component",
                    selector=comp_name,
                    text=" ".join(child_texts),
                ))

        movie_player = soup.find(id="movie_player")
        if movie_player:
            chunks.append(PageChunk(
                content="YT_COMPONENT: #movie_player (YouTube video player element)",
                element_type="yt_component",
                selector="#movie_player",
                text="YouTube video player",
            ))

        return chunks

    def _element_to_selector(self, el: Any) -> str:
        """
        Generate best CSS selector for a BeautifulSoup element.
        Priority: #id > [data-testid] > [aria-label*=...] > tag.class1.class2 > tag
        """
        try:
            el_id = el.get("id")
            if el_id and isinstance(el_id, str) and el_id.strip():
                return f"#{el_id.strip()}"

            testid = el.get("data-testid")
            if testid and isinstance(testid, str) and testid.strip():
                return f'[data-testid="{testid.strip()}"]'

            aria = el.get("aria-label")
            if aria and isinstance(aria, str) and aria.strip() and len(aria) < 60:
                safe = aria.strip().replace('"', '\\"')
                return f'[aria-label*="{safe}"]'

            classes = el.get("class", [])
            if classes and isinstance(classes, list):
                valid = [c for c in classes[:3] if c and re.match(r'^[a-zA-Z_-]', c)]
                if valid:
                    cls_str = ".".join(valid)
                    return f"{el.name}.{cls_str}" if el.name else f".{cls_str}"

            return el.name or "unknown"
        except Exception:
            return "unknown"

    # ── Storage backends ──────────────────────────────────────────────────────

    async def _upsert_to_qdrant(
        self,
        chunks: List[PageChunk],
        collection_name: str,
        url: str,
        title: str,
    ) -> None:
        """Embed chunks and upsert to Qdrant (creates collection if needed)."""
        if not self._qdrant or not self._embeddings:
            return

        import asyncio

        # Ensure collection exists
        try:
            self._qdrant.get_collection(collection_name)
        except Exception:
            try:
                self._qdrant.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=self.VECTOR_DIM, distance=Distance.COSINE),
                )
                logger.info(f"[vectorizer] Created Qdrant collection: {collection_name}")
            except Exception as e:
                logger.warning(f"[vectorizer] Failed to create Qdrant collection: {e}")
                return

        # Embed all chunk contents
        try:
            texts = [c.content for c in chunks]
            embeddings = await asyncio.get_event_loop().run_in_executor(
                None, self._embeddings.embed_documents, texts
            )
        except Exception as e:
            logger.warning(f"[vectorizer] Embedding error: {e}")
            return

        # Build Qdrant points using UUID string IDs (no collision risk)
        points = []
        for chunk, vector in zip(chunks, embeddings):
            if len(vector) != self.VECTOR_DIM:
                if len(vector) < self.VECTOR_DIM:
                    vector = vector + [0.0] * (self.VECTOR_DIM - len(vector))
                else:
                    vector = vector[:self.VECTOR_DIM]
            points.append(PointStruct(
                id=chunk.chunk_id,  # UUID string — Qdrant accepts UUID format
                vector=vector,
                payload={
                    "content": chunk.content,
                    "element_type": chunk.element_type,
                    "selector": chunk.selector,
                    "text": chunk.text,
                    "url": url,
                    "title": title,
                },
            ))

        try:
            self._qdrant.upsert(collection_name=collection_name, points=points)
            logger.info(f"[vectorizer] Upserted {len(points)} chunks → {collection_name}")
        except Exception as e:
            logger.warning(f"[vectorizer] Qdrant upsert error: {e}")

    async def _build_faiss(
        self,
        chunks: List[PageChunk],
        url: str,
        title: str,
    ) -> None:
        """FAISS in-memory fallback when Qdrant is unavailable."""
        if not self._embeddings:
            return

        import asyncio
        try:
            from langchain_community.vectorstores import FAISS
            from langchain_core.documents import Document

            docs = [
                Document(
                    page_content=c.content,
                    metadata={
                        "selector": c.selector,
                        "element_type": c.element_type,
                        "text": c.text,
                    },
                )
                for c in chunks
            ]
            self._faiss_store = await asyncio.get_event_loop().run_in_executor(
                None, lambda: FAISS.from_documents(docs, self._embeddings)
            )
            logger.info(f"[vectorizer] Built FAISS store with {len(docs)} chunks (Qdrant fallback)")
        except Exception as e:
            logger.warning(f"[vectorizer] FAISS build error: {e}")
