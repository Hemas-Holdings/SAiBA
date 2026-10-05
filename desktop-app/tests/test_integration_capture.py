"""Integration test A: real devices. Opt-in, never runs in CI, never writes files."""

from __future__ import annotations

import os
import sys

import pytest

from capture.audio_format import SAMPLE_WIDTH
from capture.listen import run_listen_through
from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from capture.mixer import AudioMixer

pytestmark = pytest.mark.integration


class DiscardSink:
    """In-memory sink so a live session still never touches disk."""

    def __init__(self) -> None:
        self.bytes_heard = 0

    def write(self, chunk: bytes) -> None:
        self.bytes_heard += len(chunk)

    def close(self) -> None:
        return None


@pytest.mark.skipif(sys.platform != "win32", reason="WASAPI loopback is Windows-only")
@pytest.mark.skipif(os.environ.get("RUN_LIVE_AUDIO") != "1", reason="live audio is opt-in")
@pytest.mark.asyncio
async def test_live_mixed_capture_yields_pcm() -> None:
    sink = DiscardSink()
    await run_listen_through(
        duration_seconds=1.0,
        sink=sink,
        loopback=LoopbackCapture(),
        microphone=MicCapture(),
        mixer=AudioMixer(),
    )
    assert sink.bytes_heard >= SAMPLE_WIDTH
