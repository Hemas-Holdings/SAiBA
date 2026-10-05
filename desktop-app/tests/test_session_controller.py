"""Segment B1: local session control. Pause is a capture boundary."""

from __future__ import annotations

import ast
import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest

from capture.audio_format import FRAMES_PER_CHUNK, SAMPLE_RATE, SAMPLE_WIDTH, silence
from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from capture.mixer import AudioMixer
from session.buffer import BufferRefused, StreamBuffer
from session.controller import InvalidSessionTransition, SessionController, SessionState

_SESSION_ROOT = Path(__file__).resolve().parents[1] / "session"
_FORBIDDEN_IMPORTS = {
    "socket",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib",
    "requests",
    "http",
    "transport",
}


class ScriptedBackend:
    """In-memory device. Reopens after close. Optionally blocks or fails."""

    def __init__(self, *, block_after: int | None = None, pattern: bytes | None = None) -> None:
        self.sample_rate = SAMPLE_RATE
        self.channels = 1
        self.sample_width = SAMPLE_WIDTH
        self.opened = False
        self.close_calls = 0
        self.fail_open = False
        self._pattern = pattern
        self._block_after = block_after
        self._reads = 0
        self._release = threading.Event()
        self.entered_block = threading.Event()
        self.release_failure = threading.Event()
        self.fail_after: int | None = None

    def open(self) -> None:
        if self.fail_open:
            raise RuntimeError("device unavailable")
        if self.opened:
            raise RuntimeError("backend is already open")
        self.opened = True
        self._reads = 0
        self._release = threading.Event()
        self.entered_block.clear()

    def read(self, frame_count: int) -> bytes:
        if not self.opened:
            raise RuntimeError("backend is not open")
        self._reads += 1
        if self.fail_after is not None and self._reads > self.fail_after:
            self.entered_block.set()
            self.release_failure.wait(timeout=2)
            if not self.opened:
                raise RuntimeError("backend is not open")
            raise RuntimeError("device exploded")
        if self._block_after is not None and self._reads > self._block_after:
            self.entered_block.set()
            while self.opened and not self._release.is_set():
                self._release.wait(timeout=0.02)
            raise RuntimeError("backend is not open")
        if self._pattern is not None:
            return self._pattern
        return silence(frame_count)

    def close(self) -> None:
        self.opened = False
        self.close_calls += 1
        self._release.set()
        self.release_failure.set()


def _controller(
    loopback: ScriptedBackend | None = None,
    microphone: ScriptedBackend | None = None,
    buffer: StreamBuffer | None = None,
) -> tuple[SessionController, ScriptedBackend, ScriptedBackend, StreamBuffer]:
    loop = loopback if loopback is not None else ScriptedBackend()
    mic = microphone if microphone is not None else ScriptedBackend()
    stream = buffer if buffer is not None else StreamBuffer(max_seconds=1)
    controller = SessionController(
        LoopbackCapture(backend=loop),
        MicCapture(backend=mic),
        AudioMixer(),
        stream,
    )
    return controller, loop, mic, stream


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for session condition")
        await asyncio.sleep(0.01)


async def _collect(controller: SessionController, dest: list[bytes]) -> None:
    async for chunk in controller.chunks():
        dest.append(chunk)


@pytest.mark.asyncio
async def test_illegal_transitions_from_idle() -> None:
    controller, _loop, _mic, _buffer = _controller()
    with pytest.raises(InvalidSessionTransition, match="cannot pause from idle"):
        await controller.pause()
    with pytest.raises(InvalidSessionTransition, match="cannot resume from idle"):
        await controller.resume()
    with pytest.raises(InvalidSessionTransition, match="cannot stop from idle"):
        await controller.stop()
    assert controller.state is SessionState.IDLE
    assert controller.session_id is None


@pytest.mark.asyncio
async def test_pause_stops_yields_and_resume_keeps_session_id() -> None:
    controller, loop, mic, buffer = _controller()
    yielded: list[bytes] = []
    await controller.start()
    consumer = asyncio.create_task(_collect(controller, yielded))
    await _wait_until(lambda: len(yielded) >= 1)
    session_id = controller.session_id
    assert session_id is not None
    assert loop.opened is True
    assert mic.opened is True

    await controller.pause()
    frozen = len(yielded)
    await asyncio.sleep(0.05)
    assert len(yielded) == frozen
    assert controller.state is SessionState.PAUSED
    assert controller.session_id == session_id
    assert loop.opened is False
    assert mic.opened is False
    assert loop.close_calls >= 1
    assert mic.close_calls >= 1
    assert len(buffer) == 0
    with pytest.raises(BufferRefused):
        buffer.write(b"\x00\x00")

    with pytest.raises(InvalidSessionTransition, match="cannot pause from paused"):
        await controller.pause()
    with pytest.raises(InvalidSessionTransition, match="cannot start from paused"):
        await controller.start()

    await controller.resume()
    assert controller.state is SessionState.RUNNING
    assert controller.session_id == session_id
    assert loop.opened is True
    assert mic.opened is True
    await _wait_until(lambda: len(yielded) > frozen)

    await controller.stop()
    assert controller.state is SessionState.IDLE
    assert controller.session_id is None
    assert len(buffer) == 0
    assert loop.opened is False
    assert mic.opened is False
    await asyncio.wait_for(consumer, timeout=2)
    after_stop = len(yielded)
    await asyncio.sleep(0.05)
    assert len(yielded) == after_stop


@pytest.mark.asyncio
async def test_stop_from_paused_tears_down() -> None:
    controller, loop, mic, buffer = _controller()
    await controller.start()
    session_id = controller.session_id
    await controller.pause()
    await controller.stop()
    assert controller.state is SessionState.IDLE
    assert controller.session_id is None
    assert session_id is not None
    assert len(buffer) == 0
    assert loop.opened is False
    assert mic.opened is False


@pytest.mark.asyncio
async def test_start_after_stop_is_a_new_session() -> None:
    controller, _loop, _mic, _buffer = _controller()
    await controller.start()
    first = controller.session_id
    await controller.stop()
    await controller.start()
    assert controller.session_id is not None
    assert controller.session_id != first
    with pytest.raises(InvalidSessionTransition, match="cannot start from running"):
        await controller.start()
    with pytest.raises(InvalidSessionTransition, match="cannot resume from running"):
        await controller.resume()
    await controller.stop()


@pytest.mark.asyncio
async def test_start_device_failure_leaves_idle() -> None:
    loop = ScriptedBackend()
    mic = ScriptedBackend()
    mic.fail_open = True
    controller, loop, mic, buffer = _controller(loop, mic)
    with pytest.raises(RuntimeError, match="device unavailable"):
        await controller.start()
    assert controller.state is SessionState.IDLE
    assert controller.session_id is None
    assert len(buffer) == 0
    assert loop.opened is False
    assert mic.opened is False


@pytest.mark.asyncio
async def test_loopback_open_failure_leaves_idle() -> None:
    loop = ScriptedBackend()
    loop.fail_open = True
    controller, loop, mic, buffer = _controller(loop, ScriptedBackend())
    with pytest.raises(RuntimeError, match="device unavailable"):
        await controller.start()
    assert controller.state is SessionState.IDLE
    assert controller.session_id is None
    assert len(buffer) == 0
    assert loop.opened is False
    assert mic.opened is False


@pytest.mark.asyncio
async def test_resume_failure_stays_paused_then_can_resume() -> None:
    controller, loop, _mic, buffer = _controller()
    await controller.start()
    session_id = controller.session_id
    await controller.pause()
    loop.fail_open = True
    with pytest.raises(RuntimeError, match="device unavailable"):
        await controller.resume()
    assert controller.state is SessionState.PAUSED
    assert controller.session_id == session_id
    assert len(buffer) == 0
    assert loop.opened is False
    loop.fail_open = False
    await controller.resume()
    assert controller.state is SessionState.RUNNING
    assert controller.session_id == session_id
    await controller.stop()


@pytest.mark.asyncio
async def test_pause_unblocks_a_warm_device() -> None:
    loop = ScriptedBackend(block_after=1)
    mic = ScriptedBackend(block_after=1)
    controller, loop, mic, buffer = _controller(loop, mic)
    await controller.start()
    await asyncio.wait_for(asyncio.to_thread(loop.entered_block.wait), timeout=2)
    await asyncio.wait_for(controller.pause(), timeout=2)
    assert controller.state is SessionState.PAUSED
    assert loop.opened is False
    assert mic.opened is False
    assert len(buffer) == 0
    with pytest.raises(BufferRefused):
        buffer.write(silence())
    await controller.stop()


@pytest.mark.asyncio
async def test_capture_failure_while_running_fails_closed() -> None:
    loop = ScriptedBackend()
    loop.fail_after = 1
    controller, loop, mic, buffer = _controller(loop, ScriptedBackend())
    yielded: list[bytes] = []
    await controller.start()
    consumer = asyncio.create_task(_collect(controller, yielded))
    await asyncio.wait_for(asyncio.to_thread(loop.entered_block.wait), timeout=2)
    loop.release_failure.set()
    await _wait_until(lambda: controller.state is SessionState.PAUSED)
    frozen = len(yielded)
    await asyncio.sleep(0.05)
    assert len(yielded) == frozen
    assert len(buffer) == 0
    assert loop.opened is False
    assert mic.opened is False
    assert controller.session_id is not None
    await controller.stop()
    await asyncio.wait_for(consumer, timeout=2)


@pytest.mark.asyncio
async def test_chunks_iterator_ends_immediately_when_idle() -> None:
    controller, _loop, _mic, _buffer = _controller()
    seen: list[bytes] = []
    await _collect(controller, seen)
    assert seen == []


@pytest.mark.asyncio
async def test_no_disk_and_logs_omit_pcm(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def reject_from_session(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("session code opened a file")

    monkeypatch.setattr("builtins.open", reject_from_session)
    monkeypatch.setattr("pathlib.Path.write_bytes", reject_from_session)
    monkeypatch.setattr("pathlib.Path.write_text", reject_from_session)

    pattern = bytes([0xAB, 0xCD]) * FRAMES_PER_CHUNK
    loop = ScriptedBackend(pattern=pattern)
    mic = ScriptedBackend(pattern=silence())
    controller, _loop, _mic, buffer = _controller(loop, mic, StreamBuffer(max_seconds=1))
    with caplog.at_level(logging.DEBUG):
        await controller.start()
        yielded: list[bytes] = []
        consumer = asyncio.create_task(_collect(controller, yielded))
        await _wait_until(lambda: len(yielded) >= 1)
        await controller.pause()
        await controller.resume()
        await controller.stop()
        await asyncio.wait_for(consumer, timeout=2)
    assert len(buffer) == 0
    text = caplog.text.lower()
    assert "\\xab" not in text
    assert "\\xcd" not in text
    for record in caplog.records:
        assert not any(isinstance(arg, (bytes, bytearray)) for arg in record.args)


@pytest.mark.asyncio
async def test_phrase_text_cannot_pause_the_session() -> None:
    controller, _loop, _mic, _buffer = _controller()
    await controller.start()
    assert not hasattr(controller, "observe")
    assert controller.state is SessionState.RUNNING
    await controller.stop()


def test_session_package_has_no_network_imports() -> None:
    for path in _SESSION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [module.split(".")[0]]
            else:
                continue
            assert _FORBIDDEN_IMPORTS.isdisjoint(names)
