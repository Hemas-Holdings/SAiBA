"""Segment A1 unit tests: LoopbackCapture with a mocked WASAPI backend."""

from __future__ import annotations

import sys

import pytest

from capture.audio_format import CHUNK_BYTES, SAMPLE_RATE, silence
from capture.loopback import LoopbackCapture, PyAudioWPatchBackend
from tests.helpers import MockCaptureBackend


@pytest.mark.asyncio
async def test_start_yields_canonical_chunks() -> None:
    backend = MockCaptureBackend(chunks=[silence(), silence()])
    capture = LoopbackCapture(backend=backend)
    gen = capture.start()
    first = await anext(gen)
    second = await anext(gen)
    capture.stop()
    assert backend.opened is False
    assert backend.close_calls >= 1
    assert len(first) == CHUNK_BYTES
    assert len(second) == CHUNK_BYTES
    await gen.aclose()


@pytest.mark.asyncio
async def test_stop_releases_device_handle() -> None:
    backend = MockCaptureBackend()
    capture = LoopbackCapture(backend=backend)
    gen = capture.start()
    await anext(gen)
    assert backend.opened is True
    capture.stop()
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
    assert backend.close_calls >= 1
    assert backend.opened is False


@pytest.mark.asyncio
async def test_double_start_raises() -> None:
    backend = MockCaptureBackend()
    capture = LoopbackCapture(backend=backend)
    gen = capture.start()
    await anext(gen)
    with pytest.raises(RuntimeError, match="already started"):
        await anext(capture.start())
    capture.stop()
    await gen.aclose()


@pytest.mark.asyncio
async def test_read_error_without_stop_propagates() -> None:
    class BoomBackend(MockCaptureBackend):
        def read(self, frame_count: int) -> bytes:
            raise RuntimeError("device exploded")

    capture = LoopbackCapture(backend=BoomBackend(chunks=[]))
    with pytest.raises(RuntimeError, match="device exploded"):
        await anext(capture.start())


@pytest.mark.asyncio
async def test_read_error_after_stop_ends_stream() -> None:
    class ClosingBackend(MockCaptureBackend):
        def read(self, frame_count: int) -> bytes:
            if self.close_calls:
                raise RuntimeError("device closed")
            return super().read(frame_count)

    backend = ClosingBackend(chunks=[silence()])
    capture = LoopbackCapture(backend=backend)
    gen = capture.start()
    await anext(gen)
    capture.stop()
    with pytest.raises(StopAsyncIteration):
        await anext(gen)


def test_real_loopback_backend_requires_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows has WASAPI; this assertion is for other platforms")
    backend = PyAudioWPatchBackend()
    with pytest.raises(RuntimeError, match="Windows"):
        backend.open()


def test_loopback_read_without_open_raises() -> None:
    backend = PyAudioWPatchBackend()
    with pytest.raises(RuntimeError, match="not open"):
        backend.read(512)


def test_canonical_sample_rate_constant() -> None:
    assert SAMPLE_RATE == 16_000


class _FakePyAudioModule:
    paWASAPI = 13


class _FakePyAudio:
    def __init__(self, speakers: dict, loopbacks: list[dict]) -> None:
        self._speakers = speakers
        self._loopbacks = loopbacks

    def get_host_api_info_by_type(self, _host_api: int) -> dict:
        return {"defaultOutputDevice": 1}

    def get_device_info_by_index(self, _index: int) -> dict:
        return self._speakers

    def get_loopback_device_info_generator(self):
        yield from self._loopbacks


def test_find_loopback_device_matches_default_speakers() -> None:
    backend = PyAudioWPatchBackend()
    backend._pyaudio = _FakePyAudio(
        speakers={"name": "Speakers", "isLoopbackDevice": False, "index": 1},
        loopbacks=[
            {
                "name": "Speakers [Loopback]",
                "index": 7,
                "defaultSampleRate": 48000,
                "maxInputChannels": 2,
            }
        ],
    )
    device = backend._find_loopback_device(_FakePyAudioModule())
    assert device["index"] == 7


def test_find_loopback_device_when_default_is_already_loopback() -> None:
    backend = PyAudioWPatchBackend()
    speakers = {"name": "Speakers [Loopback]", "isLoopbackDevice": True, "index": 3}
    backend._pyaudio = _FakePyAudio(speakers=speakers, loopbacks=[])
    assert backend._find_loopback_device(_FakePyAudioModule())["index"] == 3


def test_find_loopback_device_raises_when_missing() -> None:
    backend = PyAudioWPatchBackend()
    backend._pyaudio = _FakePyAudio(
        speakers={"name": "Speakers", "isLoopbackDevice": False, "index": 1},
        loopbacks=[{"name": "Other Device [Loopback]", "index": 9}],
    )
    with pytest.raises(RuntimeError, match="no WASAPI loopback device"):
        backend._find_loopback_device(_FakePyAudioModule())


def test_frames_per_buffer_scales_with_device_rate() -> None:
    backend = PyAudioWPatchBackend()
    backend.sample_rate = 48_000
    assert backend._frames_per_buffer() == 1536


def test_loopback_backend_open_read_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeStream:
        def read(self, frame_count: int, exception_on_overflow: bool = False) -> bytes:
            return silence(frame_count)

        def stop_stream(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakePyAudio:
        def get_host_api_info_by_type(self, _host_api: int) -> dict:
            return {"defaultOutputDevice": 0}

        def get_device_info_by_index(self, _index: int) -> dict:
            return {
                "name": "Speakers [Loopback]",
                "isLoopbackDevice": True,
                "index": 0,
                "defaultSampleRate": 16_000,
                "maxInputChannels": 1,
            }

        def get_loopback_device_info_generator(self):
            return iter(())

        def open(self, **_kwargs: object) -> FakeStream:
            return FakeStream()

        def terminate(self) -> None:
            return None

    fake_module = type(sys)("pyaudiowpatch")
    fake_module.PyAudio = FakePyAudio  # type: ignore[attr-defined]
    fake_module.paInt16 = 8  # type: ignore[attr-defined]
    fake_module.paWASAPI = 13  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_module)

    backend = PyAudioWPatchBackend()
    backend.open()
    assert len(backend.read(512)) == 1024
    backend.close()
    backend.close()
