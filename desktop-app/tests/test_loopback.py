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
        def get_read_available(self) -> int:
            return 4096

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


_TONE = bytes([1, 0])  # one int16 sample of value 1: recognisably not silence


class _ScriptedStream:
    """A loopback stream whose available frame counts are scripted per poll."""

    def __init__(self, available: list[int], channels: int = 1) -> None:
        self._available = list(available)
        self._channels = channels
        self.reads: list[int] = []
        self.closed = False

    def get_read_available(self) -> int:
        if len(self._available) > 1:
            return self._available.pop(0)
        return self._available[0]

    def read(self, frame_count: int, exception_on_overflow: bool = False) -> bytes:
        self.reads.append(frame_count)
        return _TONE * frame_count * self._channels

    def stop_stream(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _backend_with(stream: _ScriptedStream, channels: int = 1) -> tuple[PyAudioWPatchBackend, _FakeClock]:
    clock = _FakeClock()
    backend = PyAudioWPatchBackend(clock=clock, sleep=clock.sleep)
    backend.sample_rate = 16_000
    backend.channels = channels
    backend._stream = stream
    return backend, clock


def test_read_returns_device_audio_when_enough_has_arrived() -> None:
    stream = _ScriptedStream([600])
    backend, clock = _backend_with(stream)
    assert backend.read(512) == _TONE * 512
    assert stream.reads == [512]
    assert clock.sleeps == []


def test_silent_speakers_yield_silence_in_real_time_instead_of_blocking() -> None:
    # WASAPI loopback delivers nothing while nothing plays. The read must not
    # hang: after one chunk's duration it returns a chunk of silence.
    stream = _ScriptedStream([0])
    backend, clock = _backend_with(stream, channels=2)
    assert backend.read(512) == bytes(512 * 2 * 2)
    assert stream.reads == []
    assert clock.now == pytest.approx(512 / 16_000, abs=0.01)


def test_partial_audio_is_kept_and_padded_with_silence() -> None:
    stream = _ScriptedStream([0, 100])
    backend, _clock = _backend_with(stream)
    data = backend.read(512)
    assert stream.reads == [100]
    assert data[:200] == _TONE * 100
    assert data[200:] == bytes((512 - 100) * 2)


def test_audio_arriving_before_the_deadline_is_returned_whole() -> None:
    stream = _ScriptedStream([0, 0, 512])
    backend, clock = _backend_with(stream)
    assert backend.read(512) == _TONE * 512
    assert stream.reads == [512]
    assert len(clock.sleeps) == 2


def test_silent_reads_keep_real_time_despite_oversleeping() -> None:
    # Each sleep overshoots by 4 ms. Loopback must still deliver audio at the
    # real-time rate, or it falls behind the microphone it is mixed with.
    stream = _ScriptedStream([0])
    clock = _FakeClock()

    def oversleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds + 0.004

    backend = PyAudioWPatchBackend(clock=clock, sleep=oversleep)
    backend.sample_rate = 16_000
    backend._stream = stream
    chunk_seconds = 512 / 16_000
    for _ in range(200):
        backend.read(512)
    assert clock.now == pytest.approx(200 * chunk_seconds, abs=chunk_seconds)


def test_schedule_restarts_after_a_long_gap() -> None:
    stream = _ScriptedStream([0])
    backend, clock = _backend_with(stream)
    backend.read(512)
    clock.now += 30.0  # e.g. capture was paused
    started = clock.now
    backend.read(512)
    assert clock.now - started == pytest.approx(512 / 16_000, abs=0.01)


def test_read_after_close_raises() -> None:
    stream = _ScriptedStream([0])
    backend, _clock = _backend_with(stream)
    backend.close()
    assert stream.closed
    with pytest.raises(RuntimeError, match="not open"):
        backend.read(512)


def test_close_waits_for_a_read_in_progress() -> None:
    # Closing the device while another thread was inside read() crashed the
    # process. Close must wait until that read has returned.
    import threading

    entered = threading.Event()
    release = threading.Event()
    events: list[str] = []

    class SlowStream(_ScriptedStream):
        def read(self, frame_count: int, exception_on_overflow: bool = False) -> bytes:
            entered.set()
            release.wait(timeout=5)
            events.append("read returned")
            return super().read(frame_count)

        def close(self) -> None:
            events.append("closed")
            super().close()

    stream = SlowStream([512])
    backend = PyAudioWPatchBackend()
    backend.sample_rate = 16_000
    backend._stream = stream
    reader = threading.Thread(target=backend.read, args=(512,))
    reader.start()
    assert entered.wait(timeout=5)
    closer = threading.Thread(target=backend.close)
    closer.start()
    closer.join(timeout=0.2)
    assert closer.is_alive(), "close did not wait for the read"
    release.set()
    reader.join(timeout=5)
    closer.join(timeout=5)
    assert events == ["read returned", "closed"]
