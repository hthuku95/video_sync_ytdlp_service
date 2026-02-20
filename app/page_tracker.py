"""
PageTracker — PostgreSQL metadata tracking for Playwright agent page visits and download attempts.

Tracks every page the agent visits and every CDN URL it attempts to download.

Tables created automatically on first use (CREATE TABLE IF NOT EXISTS):
  - playwright_page_visits        — one row per page URL the agent navigates to
  - playwright_download_attempts  — one row per CDN URL the agent tries to download

Useful for:
  - Debugging failed downloads (which pages did the agent visit? what CDN URLs were tried?)
  - Monitoring agent behaviour across jobs
  - Auditing download attempts

Fallback: if asyncpg is unavailable or DATABASE_URL is not set, all tracking silently no-ops.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Availability flag ────────────────────────────────────────────────────────

DB_TRACKING_AVAILABLE = False

try:
    import asyncpg  # noqa: F401
    DB_TRACKING_AVAILABLE = True
    logger.info("✅ asyncpg available (PostgreSQL page visit tracking enabled)")
except ImportError:
    logger.warning(
        "⚠️ asyncpg not installed — page visit tracking disabled "
        "(install asyncpg>=0.29.0 and set DATABASE_URL)"
    )


# ── PageTracker ───────────────────────────────────────────────────────────────

class PageTracker:
    """
    Async PostgreSQL client for tracking Playwright agent page visits and download attempts.

    Usage:
        tracker = PageTracker(database_url)
        await tracker.ensure_tables()
        await tracker.track_page_visit(job_id, video_url, page_url, title, chunks_count, collection)
        await tracker.track_download_attempt(job_id, cdn_url, success, error, size_bytes)
        await tracker.close()
    """

    CREATE_TABLES_SQL = """
        CREATE TABLE IF NOT EXISTS playwright_page_visits (
            id              BIGSERIAL PRIMARY KEY,
            job_id          TEXT        NOT NULL,
            video_url       TEXT,
            page_url        TEXT        NOT NULL,
            page_title      TEXT,
            chunks_count    INT         DEFAULT 0,
            collection_name TEXT,
            visited_at      TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_playwright_page_visits_job_id
            ON playwright_page_visits (job_id);

        CREATE INDEX IF NOT EXISTS idx_playwright_page_visits_visited_at
            ON playwright_page_visits (visited_at DESC);

        CREATE TABLE IF NOT EXISTS playwright_download_attempts (
            id              BIGSERIAL PRIMARY KEY,
            job_id          TEXT        NOT NULL,
            cdn_url         TEXT,
            success         BOOLEAN     NOT NULL,
            error_msg       TEXT,
            size_bytes      BIGINT,
            attempted_at    TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_playwright_download_attempts_job_id
            ON playwright_download_attempts (job_id);

        CREATE INDEX IF NOT EXISTS idx_playwright_download_attempts_attempted_at
            ON playwright_download_attempts (attempted_at DESC);
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url: Optional[str] = database_url or os.getenv("DATABASE_URL")
        self._pool: Optional[Any] = None
        self._tables_ready: bool = False

    async def ensure_tables(self) -> bool:
        """
        Create tracking tables if they don't exist.
        Returns True if tables are ready, False if database is unavailable.
        """
        if not DB_TRACKING_AVAILABLE or not self._database_url:
            return False

        try:
            pool = await self._get_pool()
            if pool is None:
                return False
            async with pool.acquire() as conn:
                await conn.execute(self.CREATE_TABLES_SQL)
            self._tables_ready = True
            logger.info(
                "[tracker] ✅ PostgreSQL tracking tables ready "
                "(playwright_page_visits, playwright_download_attempts)"
            )
            return True
        except Exception as e:
            logger.warning(f"[tracker] Failed to create tracking tables: {e}")
            return False

    async def track_page_visit(
        self,
        job_id: str,
        video_url: str,
        page_url: str,
        page_title: str,
        chunks_count: int,
        collection_name: str,
    ) -> None:
        """
        Record that the agent visited a page.
        Silent no-op if PostgreSQL is unavailable.
        """
        if not self._tables_ready:
            return

        try:
            pool = await self._get_pool()
            if pool is None:
                return
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO playwright_page_visits
                        (job_id, video_url, page_url, page_title, chunks_count, collection_name)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    job_id,
                    video_url[:500] if video_url else None,
                    page_url[:500],
                    page_title[:500] if page_title else None,
                    chunks_count,
                    collection_name[:200] if collection_name else None,
                )
            logger.debug(f"[tracker] Tracked page visit: {page_url[:60]} (job={job_id})")
        except Exception as e:
            logger.debug(f"[tracker] track_page_visit failed (non-critical): {e}")

    async def track_download_attempt(
        self,
        job_id: str,
        cdn_url: Optional[str],
        success: bool,
        error: Optional[str],
        size_bytes: Optional[int],
    ) -> None:
        """
        Record a CDN download attempt (success or failure).
        Silent no-op if PostgreSQL is unavailable.
        """
        if not self._tables_ready:
            return

        try:
            pool = await self._get_pool()
            if pool is None:
                return
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO playwright_download_attempts
                        (job_id, cdn_url, success, error_msg, size_bytes)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    job_id,
                    cdn_url[:500] if cdn_url else None,
                    success,
                    error[:500] if error else None,
                    size_bytes,
                )
            logger.debug(f"[tracker] Tracked download attempt: success={success} (job={job_id})")
        except Exception as e:
            logger.debug(f"[tracker] track_download_attempt failed (non-critical): {e}")

    async def close(self) -> None:
        """Close the asyncpg connection pool."""
        if self._pool is not None:
            try:
                await self._pool.close()
                logger.debug("[tracker] asyncpg pool closed")
            except Exception:
                pass
            finally:
                self._pool = None

    async def _get_pool(self) -> Optional[Any]:
        """Get or create asyncpg connection pool. Returns None if unavailable."""
        if not DB_TRACKING_AVAILABLE or not self._database_url:
            return None

        if self._pool is None:
            try:
                # asyncpg accepts standard postgresql:// DSN
                # Neon's channel_binding=require is handled transparently
                self._pool = await asyncpg.create_pool(
                    self._database_url,
                    min_size=1,
                    max_size=3,
                    command_timeout=10,
                )
                logger.info("[tracker] asyncpg connection pool created")
            except Exception as e:
                logger.warning(f"[tracker] Failed to create asyncpg pool: {e}")
                return None

        return self._pool
