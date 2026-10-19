"""Session streamer: local control, audio only while running, no flush on failure."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from session.controller import SessionState
from transport.client import BackendClient, TransportError
from transport.streamer import SessionStreamer
from tests.test_transport_client import FakeConnector, FakeSocket


class FakeSession:
    def __init__(self, chunks: list[bytes]) -> None:
        self.session_id: str | None = None
        self.state = SessionState.IDLE
        self.pause_calls = 0
        self._chunks = chunks

    async def start(self) -> SessionState:
        self.session_id = "session-1"
        self.state = SessionState.RUNNING
        return self.state

    async def pause(self) -> SessionState:
        self.pause_calls += 1
        self.state = SessionState.PAUSED
        return self.state

    async def resume(self) -> SessionState:
        self.state = SessionState.RUNNING
        return self.state

    async def stop(self) -> SessionState:
        self.state = SessionState.IDLE
        self.session_id = None
        return self.state

    async def chunks(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self.state is SessionState.IDLE:
                return
            while self.state is not SessionState.RUNNING:
                await _nap()
            yield chunk
        while self.state is not SessionState.IDLE:
            await _nap()


async def _nap() -> None:
    import asyncio

    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_streamer_forwards_until_pause() -> None:
    socket = FakeSocket()
    client = BackendClient(FakeConnector(socket))
    session = FakeSession([b"\x01\x02", b"\x03\x04"])
    streamer = SessionStreamer(session, client)
    await streamer.start()
    await _wait_for(lambda: b"\x03\x04" in socket.sent)
    await streamer.pause()
    assert session.state is SessionState.PAUSED
    assert json.loads([item for item in socket.sent if isinstance(item, str)][-1])["action"] == "pause"
    await streamer.stop()


@pytest.mark.asyncio
async def test_streamer_does_not_flush_after_send_failure() -> None:
    socket = FakeSocket()
    client = BackendClient(FakeConnector(socket))
    session = FakeSession([b"\x01\x00", b"\x02\x00", b"\x03\x00"])
    socket.fail_on = b"\x02\x00"
    streamer = SessionStreamer(session, client)
    await streamer.start()
    await _wait_for(lambda: b"\x01\x00" in socket.sent)
    await _nap()
    binary = [item for item in socket.sent if isinstance(item, bytes)]
    assert binary == [b"\x01\x00"]
    await streamer.stop()


@pytest.mark.asyncio
async def test_start_failure_pauses_local_capture() -> None:
    socket = FakeSocket()

    async def reject(self: FakeSocket) -> str:
        del self
        return '{"ok":false,"error":"rejected"}'

    socket.recv = reject.__get__(socket, FakeSocket)  # type: ignore[method-assign]
    client = BackendClient(FakeConnector(socket))
    session = FakeSession([b"\x01\x02"])
    streamer = SessionStreamer(session, client)
    with pytest.raises(TransportError):
        await streamer.start()
    assert session.pause_calls == 1
    assert b"\x01\x02" not in socket.sent


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("streamer condition not met")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_capture_is_silenced_while_an_answer_plays() -> None:
    socket = FakeSocket()
    client = BackendClient(FakeConnector(socket))
    session = FakeSession([b"\x05\x06", b"\x07\x08", b"\x09\x0a"])
    muted = {"on": False}

    def is_muted() -> bool:
        return muted["on"]

    streamer = SessionStreamer(session, client, muted=is_muted)
    muted["on"] = True
    await streamer.start()
    await _wait_for(lambda: len([s for s in socket.sent if isinstance(s, bytes)]) == 3)
    audio = [s for s in socket.sent if isinstance(s, bytes)]
    assert audio == [b"\x00\x00", b"\x00\x00", b"\x00\x00"]
    await streamer.stop()


@pytest.mark.asyncio
async def test_playback_starts_with_the_session_and_outlives_stop() -> None:
    import asyncio

    class Playback:
        def __init__(self) -> None:
            self.session_ids: list[str] = []
            self.release = asyncio.Event()
            self.finished = False

        async def run(self, session_id: str) -> None:
            self.session_ids.append(session_id)
            await self.release.wait()
            self.finished = True

    socket = FakeSocket()
    client = BackendClient(FakeConnector(socket))
    playback = Playback()
    streamer = SessionStreamer(FakeSession([b"\x01\x02"]), client, playback=playback)
    await streamer.start()
    await _wait_for(lambda: playback.session_ids == ["session-1"])
    await streamer.pause()
    await streamer.resume()
    await streamer.stop()
    assert not playback.finished, "Stop must not cut playback"
    playback.release.set()
    await streamer.wait_playback()
    assert playback.finished


@pytest.mark.asyncio
async def test_wait_playback_gives_up_after_the_grace_period() -> None:
    import asyncio

    class Stuck:
        async def run(self, session_id: str) -> None:
            await asyncio.Event().wait()

    client = BackendClient(FakeConnector(FakeSocket()))
    streamer = SessionStreamer(FakeSession([]), client, playback=Stuck(), playback_grace=0.05)
    await streamer.start()
    await streamer.stop()
    await asyncio.wait_for(streamer.wait_playback(), timeout=1)
    await streamer.wait_playback()
