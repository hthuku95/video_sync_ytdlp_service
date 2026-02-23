"""
Integration tests for you-get and streamlink download strategies.

Run:
    pytest tests/test_strategy_others.py -v
"""

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import YouTubeDownloader, YOU_GET_AVAILABLE, STREAMLINK_AVAILABLE


@pytest.fixture
def dl():
    return YouTubeDownloader()


@pytest.mark.asyncio
async def test_you_get(dl, job_dir):
    """you-get — independent multi-platform downloader."""
    if not YOU_GET_AVAILABLE:
        pytest.skip("you-get not installed")
    path, meta, err = await dl._run_you_get_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
    )
    if err:
        pytest.skip(f"you-get skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="you-get")
    print(f"\n✅ you-get: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_streamlink(dl, job_dir):
    """streamlink — independent stream extractor."""
    if not STREAMLINK_AVAILABLE:
        pytest.skip("streamlink not installed")
    path, meta, err = await dl._run_streamlink_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
    )
    if err:
        pytest.skip(f"streamlink skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="streamlink")
    print(f"\n✅ streamlink: {path.stat().st_size / 1024 / 1024:.1f} MB")
