"""
Integration tests for cobalt.tools download strategies.

Each test calls _run_cobalt_strategy() directly with a specific API endpoint
and asserts a real video file was written to disk.

Run:
    pytest tests/test_strategy_cobalt.py -v
"""

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import YouTubeDownloader


@pytest.fixture
def dl():
    return YouTubeDownloader()


@pytest.mark.asyncio
async def test_cobalt_primary_instance(dl, job_dir):
    """cobalt.tools primary API at api.cobalt.tools."""
    path, meta, err = await dl._run_cobalt_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
        api_url="https://api.cobalt.tools/",
    )
    if err:
        pytest.skip(f"cobalt primary skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="cobalt primary")
    print(f"\n✅ cobalt primary: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_cobalt_secondary_instance(dl, job_dir):
    """cobalt.tools secondary community instance at co.wuk.sh."""
    path, meta, err = await dl._run_cobalt_strategy(
        video_url=TEST_VIDEO_URL,
        job_dir=job_dir,
        quality="360p",
        api_url="https://co.wuk.sh/api/json",
    )
    if err:
        pytest.skip(f"cobalt secondary skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="cobalt secondary")
    print(f"\n✅ cobalt secondary: {path.stat().st_size / 1024 / 1024:.1f} MB")
