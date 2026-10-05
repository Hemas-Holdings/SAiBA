"""Listen-through must play mixed PCM and never persist audio to disk."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from capture.audio_format import silence
from capture.listen import SoundDevicePlaybackSink, run_listen_through
from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from tests.helpers import MockCaptureBackend


class MemorySink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_listen_through_writes_only_to_sink(tmp_path: Path) -> None:
    loopback = LoopbackCapture(backend=MockCaptureBackend(chunks=[silence()] * 3))
    microphone = MicCapture(backend=MockCaptureBackend(chunks=[silence()] * 3))
    sink = MemorySink()

    await run_listen_through(
        duration_seconds=0.05,
        sink=sink,
        loopback=loopback,
        microphone=microphone,
    )

    assert sink.closed is True
    assert len(sink.chunks) >= 1
    assert all(isinstance(chunk, bytes) for chunk in sink.chunks)
    leftover_audio = list(tmp_path.glob("*"))
    assert leftover_audio == []


@pytest.mark.asyncio
async def test_listen_through_rejects_non_positive_duration() -> None:
    with pytest.raises(ValueError):
        await run_listen_through(0, MemorySink())


def test_playback_sink_writes_pcm_not_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.written: list[object] = []
            self.closed = False

        def start(self) -> None:
            return None

        def write(self, samples: object) -> None:
            self.written.append(samples)

        def stop(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    stream = FakeStream()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=lambda **_k: stream))

    sink = SoundDevicePlaybackSink()
    sink.write(silence())
    sink.write(b"")
    sink.close()
    assert len(stream.written) == 1
    assert stream.closed is True
    assert list(tmp_path.glob("*")) == []
