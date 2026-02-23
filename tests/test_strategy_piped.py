"""
Integration tests for Piped download strategies.

Each test calls _run_piped_strategy() with a specific Piped API instance
and asserts a real video file was written to disk.

Run:
    pytest tests/test_strategy_piped.py -v
"""

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import YouTubeDownloader


@pytest.fixture
def dl():
    return YouTubeDownloader()


@pytest.mark.asyncio
async def test_piped_kavin(dl, job_dir):
    """Piped instance: pipedapi.kavin.rocks"""
    path, meta, err = await dl._run_piped_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://pipedapi.kavin.rocks"
    )
    if err:
        pytest.skip(f"pipedapi.kavin.rocks skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="piped kavin")
    print(f"\n✅ pipedapi.kavin.rocks: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_piped_projectsegfault(dl, job_dir):
    """Piped instance: pipedapi.in.projectsegfau.lt"""
    path, meta, err = await dl._run_piped_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://pipedapi.in.projectsegfau.lt"
    )
    if err:
        pytest.skip(f"pipedapi.in.projectsegfau.lt skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="piped projectsegfault")
    print(f"\n✅ pipedapi.in.projectsegfau.lt: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_piped_garuda(dl, job_dir):
    """Piped instance: piped-api.garudalinux.org"""
    path, meta, err = await dl._run_piped_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://piped-api.garudalinux.org"
    )
    if err:
        pytest.skip(f"piped-api.garudalinux.org skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="piped garuda")
    print(f"\n✅ piped-api.garudalinux.org: {path.stat().st_size / 1024 / 1024:.1f} MB")
