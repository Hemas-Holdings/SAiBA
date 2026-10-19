"""Playback socket client: protocol, ordering, and failing alone (never capture)."""

from __future__ import annotations

import asyncio
import json

from playback.client import PlaybackClient


class ScriptedSocket:
    def __init__(self, frames: list[object]) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self._frames = list(frames)

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._frames:
            raise ConnectionError("closed")
        frame = self._frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame if isinstance(frame, (str, bytes)) else json.dumps(frame)

    async def close(self) -> None:
        self.closed = True


class Connector:
    def __init__(self, socket: ScriptedSocket | Exception) -> None:
        self._socket = socket

    async def connect(self) -> ScriptedSocket:
        if isinstance(self._socket, Exception):
            raise self._socket
        return self._socket


class RecordingPlayer:
    def __init__(self) -> None:
        self.answers: list[bytes] = []

    async def play(self, stream) -> int:
        data = b"".join([chunk async for chunk in stream])
        self.answers.append(data)
        return len(data)


def _start(n: int) -> dict[str, object]:
    return {"type": "answer_start", "answer_id": n, "sample_rate": 16000}


async def test_plays_answers_in_order_until_session_end() -> None:
    socket = ScriptedSocket([
        {"ok": True},
        _start(1), b"\x01\x00", b"\x02\x00", {"type": "answer_end", "answer_id": 1},
        _start(2), b"\x03\x00", {"type": "answer_end", "answer_id": 2},
        {"type": "session_end"},
    ])
    player = RecordingPlayer()
    client = PlaybackClient(Connector(socket), player)
    await client.run("session-1")
    assert json.loads(socket.sent[0]) == {"action": "subscribe", "session_id": "session-1"}
    assert len(socket.sent) == 1, "playback socket is receive-only"
    assert player.answers == [b"\x01\x00\x02\x00", b"\x03\x00"]
    assert client.ended and client.answers_played == 2
    assert socket.closed


async def test_answer_error_plays_what_arrived_and_continues() -> None:
    socket = ScriptedSocket([
        {"ok": True},
        _start(1), b"\x01\x00", {"type": "answer_error", "answer_id": 1},
        {"type": "session_end"},
    ])
    player = RecordingPlayer()
    client = PlaybackClient(Connector(socket), player)
    await client.run("s")
    assert player.answers == [b"\x01\x00"]
    assert client.ended


async def test_refused_subscribe_ends_quietly() -> None:
    socket = ScriptedSocket([{"ok": False, "error": "unknown_session"}])
    player = RecordingPlayer()
    client = PlaybackClient(Connector(socket), player)
    await client.run("s")
    assert player.answers == [] and not client.ended and socket.closed


async def test_protocol_violations_stop_playback_only() -> None:
    for frames in (
        [{"ok": True}, b"\x01\x00"],
        [{"ok": True}, {"type": "mystery"}],
        [{"ok": True}, {"type": "answer_start", "answer_id": 1, "sample_rate": 44100}],
        [{"ok": True}, _start(1), {"type": "answer_end", "answer_id": 7}],
        [{"ok": True}, "not json"],
        [{"ok": True}, "[1, 2]"],
        [b"\x00"],
    ):
        socket = ScriptedSocket(frames)
        client = PlaybackClient(Connector(socket), RecordingPlayer())
        await client.run("s")
        assert not client.ended
        assert socket.closed


async def test_dropped_socket_mid_answer_still_plays_received_audio() -> None:
    socket = ScriptedSocket([{"ok": True}, _start(1), b"\x01\x00", ConnectionError("gone")])
    player = RecordingPlayer()
    await PlaybackClient(Connector(socket), player).run("s")
    assert player.answers == [b"\x01\x00"]


async def test_connect_failure_is_contained() -> None:
    client = PlaybackClient(Connector(OSError("refused")), RecordingPlayer())
    await client.run("s")
    assert not client.ended


async def test_cancellation_propagates_and_closes() -> None:
    gate = asyncio.Event()

    class Hanging(ScriptedSocket):
        async def recv(self):
            if self._frames:
                return await super().recv()
            await gate.wait()
            return ""

    socket = Hanging([{"ok": True}])
    task = asyncio.create_task(PlaybackClient(Connector(socket), RecordingPlayer()).run("s"))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert socket.closed
