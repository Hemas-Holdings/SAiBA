"""Segment A2 unit tests: MicCapture with a mocked input device."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from capture.audio_format import CHUNK_BYTES, silence
from capture.microphone import MicCapture, SoundDeviceMicBackend
from tests.helpers import MockCaptureBackend


@pytest.mark.asyncio
async def test_mic_start_stop_lifecycle() -> None:
    backend = MockCaptureBackend(chunks=[silence(), silence()])
    capture = MicCapture(backend=backend)
    gen = capture.start()
    chunk = await anext(gen)
    assert len(chunk) == CHUNK_BYTES
    capture.stop()
    assert backend.close_calls >= 1
    await gen.aclose()


@pytest.mark.asyncio
async def test_mic_stop_releases_handle() -> None:
    backend = MockCaptureBackend()
    capture = MicCapture(backend=backend)
    gen = capture.start()
    await anext(gen)
    capture.stop()
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
    assert backend.opened is False


def test_mic_backend_read_without_open_raises() -> None:
    backend = SoundDeviceMicBackend()
    with pytest.raises(RuntimeError, match="not open"):
        backend.read(512)


def test_mic_backend_open_read_close(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            self.closed = False

        def start(self) -> None:
            self.started = True

        def read(self, frame_count: int) -> tuple[np.ndarray, bool]:
            return np.zeros((frame_count, 1), dtype=np.int16), True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    fake_stream = FakeStream()
    fake_sd = SimpleNamespace(InputStream=lambda **_kwargs: fake_stream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    backend = SoundDeviceMicBackend()
    backend.open()
    assert fake_stream.started is True
    assert len(backend.read(512)) == 1024
    backend.close()
    assert fake_stream.stopped is True
    assert fake_stream.closed is True
    backend.close()
