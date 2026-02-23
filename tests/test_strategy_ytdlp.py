"""
Integration tests for yt-dlp download strategies.

Each test calls _run_ytdlp_strategy() directly with a specific player_clients
combination and asserts a real video file was written to disk.

Run individually:
    pytest tests/test_strategy_ytdlp.py::test_ytdlp_ios -v

Run all yt-dlp tests:
    pytest tests/test_strategy_ytdlp.py -v
"""

import os

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import QUALITY_FORMATS, YouTubeDownloader


# ─── Shared fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def dl():
    return YouTubeDownloader()


# ─── yt-dlp strategy tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ytdlp_ios(dl, job_dir):
    """Strategy 1: yt-dlp iOS client — best for bypassing PO token on datacenter IPs."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["ios"],
        use_cookies=False,
        skip_webpage=True,
    )
    if err:
        pytest.skip(f"Strategy skipped (expected on blocked IPs): {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp ios")
    print(f"\n✅ yt-dlp ios: {path.stat().st_size / 1024 / 1024:.1f} MB, title={meta.title!r}")


@pytest.mark.asyncio
async def test_ytdlp_android(dl, job_dir):
    """Strategy 3: yt-dlp Android client — different extraction path."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["android"],
        use_cookies=False,
        skip_webpage=True,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp android")
    print(f"\n✅ yt-dlp android: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_tv_embedded(dl, job_dir):
    """Strategy 5: yt-dlp tv_embedded client — TV embedded player."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["tv_embedded"],
        use_cookies=False,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp tv_embedded")
    print(f"\n✅ yt-dlp tv_embedded: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_mweb(dl, job_dir):
    """Strategy 6: yt-dlp mweb client — mobile web."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["mweb"],
        use_cookies=False,
        skip_webpage=True,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp mweb")
    print(f"\n✅ yt-dlp mweb: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_web_creator(dl, job_dir):
    """Strategy 7: yt-dlp web_creator client."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["web_creator"],
        use_cookies=False,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp web_creator")
    print(f"\n✅ yt-dlp web_creator: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_web(dl, job_dir):
    """Strategy 7b: yt-dlp web client — standard web player fingerprint."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["web"],
        use_cookies=False,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp web")
    print(f"\n✅ yt-dlp web: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_web_embedded(dl, job_dir):
    """Strategy 7c: yt-dlp web_embedded client."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["web_embedded"],
        use_cookies=False,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp web_embedded")
    print(f"\n✅ yt-dlp web_embedded: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_tv(dl, job_dir):
    """Strategy 7d: yt-dlp tv client — YouTube TV app protocol."""
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["tv"],
        use_cookies=False,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp tv")
    print(f"\n✅ yt-dlp tv: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_ios_with_cookies(dl, job_dir):
    """Strategy 2: yt-dlp iOS + cookies — authenticated session."""
    if not os.getenv("YTDLP_COOKIES_B64"):
        pytest.skip("YTDLP_COOKIES_B64 not set")
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["ios"],
        use_cookies=True,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp ios+cookies")
    print(f"\n✅ yt-dlp ios+cookies: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_ytdlp_android_with_cookies(dl, job_dir):
    """Strategy 4: yt-dlp Android + cookies."""
    if not os.getenv("YTDLP_COOKIES_B64"):
        pytest.skip("YTDLP_COOKIES_B64 not set")
    path, meta, err = await dl._run_ytdlp_strategy(
        video_url=TEST_VIDEO_URL,
        output_path=job_dir / "video.mp4",
        format_selector=QUALITY_FORMATS["360p"],
        player_clients=["android"],
        use_cookies=True,
        skip_webpage=False,
    )
    if err:
        pytest.skip(f"Strategy skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="yt-dlp android+cookies")
    print(f"\n✅ yt-dlp android+cookies: {path.stat().st_size / 1024 / 1024:.1f} MB")
