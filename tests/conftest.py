"""
Shared fixtures and helpers for YTDLPAPI integration tests.

All tests download from a known-stable public video and verify a real file
was written to disk with at least 1 MB of content.
"""

import asyncio
import os
import pathlib
import sys

import pytest

# ─── Path + .env loading (must happen before any app import) ─────────────────

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# Load .env so YTDLP_COOKIES_B64, WEBSHARE_*, GOOGLE_API_KEY etc. are available
_env_file = _ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ─── Constants ───────────────────────────────────────────────────────────────

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
MIN_VIDEO_BYTES = 1_000_000  # 1 MB


# ─── Session-level setup ─────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
async def refresh_proxy_manager():
    """
    Fetch Webshare proxies once before the entire test session.
    This ensures proxy_manager._proxies is populated so every strategy
    gets a residential proxy injected (yt-dlp, cobalt, invidious, etc.).
    """
    from app.proxy_manager import proxy_manager
    await proxy_manager.refresh()
    print(f"\n🌐 Proxy manager: {len(proxy_manager._proxies)} proxies loaded")


# ─── Per-test fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def job_dir(tmp_path):
    """Temporary directory for a single download job."""
    d = tmp_path / "job"
    d.mkdir()
    return d


@pytest.fixture
def dl():
    """A fresh YouTubeDownloader instance (cookies + proxy pre-loaded)."""
    from app.downloader import YouTubeDownloader
    return YouTubeDownloader()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def assert_video_downloaded(
    file_path,
    min_bytes: int = MIN_VIDEO_BYTES,
    msg_prefix: str = "",
) -> None:
    """
    Assert that a strategy returned a real, non-empty video file.

    Args:
        file_path:  The Path returned by the strategy (may be None on failure).
        min_bytes:  Minimum acceptable file size (default: 1 MB).
        msg_prefix: Optional prefix for assertion messages.
    """
    prefix = f"{msg_prefix}: " if msg_prefix else ""
    assert file_path is not None, f"{prefix}file_path is None — strategy returned no file"
    p = pathlib.Path(file_path)
    assert p.exists(), f"{prefix}File not found on disk: {p}"
    size = p.stat().st_size
    assert size >= min_bytes, (
        f"{prefix}File too small ({size:,} bytes < {min_bytes:,} minimum). "
        f"Likely an error page or empty response."
    )
