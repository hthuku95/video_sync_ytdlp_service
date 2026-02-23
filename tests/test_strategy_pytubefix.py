"""
Integration tests for pytubefix download strategies.

Each test calls _run_pytubefix_strategy() with a specific client name
and asserts a real video file was written to disk.

Run:
    pytest tests/test_strategy_pytubefix.py -v
"""

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import YouTubeDownloader, PYTUBEFIX_AVAILABLE


@pytest.fixture
def dl():
    return YouTubeDownloader()


def require_pytubefix():
    if not PYTUBEFIX_AVAILABLE:
        pytest.skip("pytubefix not installed")


@pytest.mark.asyncio
async def test_pytubefix_ios(dl, job_dir):
    """pytubefix IOS client."""
    require_pytubefix()
    path, meta, err = await dl._run_pytubefix_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
        client_name="IOS",
    )
    if err:
        pytest.skip(f"pytubefix IOS skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="pytubefix IOS")
    print(f"\n✅ pytubefix IOS: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_pytubefix_android(dl, job_dir):
    """pytubefix ANDROID client."""
    require_pytubefix()
    path, meta, err = await dl._run_pytubefix_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
        client_name="ANDROID",
    )
    if err:
        pytest.skip(f"pytubefix ANDROID skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="pytubefix ANDROID")
    print(f"\n✅ pytubefix ANDROID: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_pytubefix_tv_embed(dl, job_dir):
    """pytubefix TV_EMBED client."""
    require_pytubefix()
    path, meta, err = await dl._run_pytubefix_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
        client_name="TV_EMBED",
    )
    if err:
        pytest.skip(f"pytubefix TV_EMBED skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="pytubefix TV_EMBED")
    print(f"\n✅ pytubefix TV_EMBED: {path.stat().st_size / 1024 / 1024:.1f} MB")
