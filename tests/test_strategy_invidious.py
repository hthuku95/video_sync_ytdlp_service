"""
Integration tests for Invidious download strategies.

Each test calls _run_invidious_strategy() with a specific instance and
asserts a real video file was written to disk.

Run:
    pytest tests/test_strategy_invidious.py -v
"""

import pytest

from .conftest import TEST_VIDEO_URL, assert_video_downloaded
from app.downloader import YouTubeDownloader


@pytest.fixture
def dl():
    return YouTubeDownloader()


@pytest.mark.asyncio
async def test_invidious_nadeko(dl, job_dir):
    """Invidious instance: inv.nadeko.net"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://inv.nadeko.net"
    )
    if err:
        pytest.skip(f"inv.nadeko.net skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious nadeko")
    print(f"\n✅ inv.nadeko.net: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_yewtu(dl, job_dir):
    """Invidious instance: yewtu.be"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://yewtu.be"
    )
    if err:
        pytest.skip(f"yewtu.be skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious yewtu")
    print(f"\n✅ yewtu.be: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_nerdvpn(dl, job_dir):
    """Invidious instance: invidious.nerdvpn.de"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://invidious.nerdvpn.de"
    )
    if err:
        pytest.skip(f"invidious.nerdvpn.de skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious nerdvpn")
    print(f"\n✅ invidious.nerdvpn.de: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_io(dl, job_dir):
    """Invidious instance: invidious.io"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://invidious.io"
    )
    if err:
        pytest.skip(f"invidious.io skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious io")
    print(f"\n✅ invidious.io: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_puffyan(dl, job_dir):
    """Invidious instance: vid.puffyan.us"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://vid.puffyan.us"
    )
    if err:
        pytest.skip(f"vid.puffyan.us skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious puffyan")
    print(f"\n✅ vid.puffyan.us: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_privacydev(dl, job_dir):
    """Invidious instance: invidious.privacydev.net"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://invidious.privacydev.net"
    )
    if err:
        pytest.skip(f"invidious.privacydev.net skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious privacydev")
    print(f"\n✅ invidious.privacydev.net: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_artemislena(dl, job_dir):
    """Invidious instance: yt.artemislena.eu"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://yt.artemislena.eu"
    )
    if err:
        pytest.skip(f"yt.artemislena.eu skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious artemislena")
    print(f"\n✅ yt.artemislena.eu: {path.stat().st_size / 1024 / 1024:.1f} MB")


@pytest.mark.asyncio
async def test_invidious_flokinet(dl, job_dir):
    """Invidious instance: invidious.flokinet.to"""
    path, meta, err = await dl._run_invidious_strategy(
        TEST_VIDEO_URL, job_dir, "360p", "https://invidious.flokinet.to"
    )
    if err:
        pytest.skip(f"invidious.flokinet.to skipped: {err[:150]}")
    assert_video_downloaded(path, msg_prefix="invidious flokinet")
    print(f"\n✅ invidious.flokinet.to: {path.stat().st_size / 1024 / 1024:.1f} MB")
