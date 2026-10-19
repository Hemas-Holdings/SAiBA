"""Receive-only playback socket: answer audio from the backend.

Separate from the capture socket in `transport.client`. It subscribes once
for the current session id, then only receives frames:

    {"type":"answer_start","answer_id":n,"sample_rate":16000}
    <binary PCM> ...
    {"type":"answer_end","answer_id":n}   or   {"type":"answer_error","answer_id":n}
    {"type":"session_end"}

Answers play one at a time. A protocol error or a dropped socket ends
playback only; it never touches capture. Pause and Stop do not cut an
answer that is playing. Audio is not written to disk or logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from capture.audio_format import SAMPLE_RATE
from transport.client import SocketConnector, TransportSocket

logger = logging.getLogger(__name__)

_END = object()


class Player(Protocol):
    async def play(self, stream: AsyncIterator[bytes]) -> int: ...


class PlaybackProtocolError(Exception):
    """The server sent something outside the playback protocol."""


class PlaybackClient:
    """Subscribe to one session's answers and play them."""

    def __init__(self, connector: SocketConnector, player: Player) -> None:
        self._connector = connector
        self._player = player
        self._socket: TransportSocket | None = None
        self.answers_played = 0
        self.ended = False

    async def run(self, session_id: str) -> None:
        """Play answers until `session_end`, a socket failure, or cancellation."""

        try:
            self._socket = await self._connector.connect()
            await self._subscribe(session_id)
            logger.info("playback subscribed")
            await self._receive()
        except asyncio.CancelledError:
            logger.info("playback cancelled")
            raise
        except PlaybackProtocolError:
            logger.warning("playback protocol error; playback stopped")
        except Exception as error:  # noqa: BLE001 - playback fails on its own
            logger.warning("playback socket failed error=%s", type(error).__name__)
        finally:
            await self._close()

    async def _subscribe(self, session_id: str) -> None:
        socket = self._require_socket()
        await socket.send(json.dumps({"action": "subscribe", "session_id": session_id}, separators=(",", ":")))
        reply = _control(await socket.recv())
        if reply.get("ok") is not True:
            raise PlaybackProtocolError("subscribe refused")

    async def _receive(self) -> None:
        socket = self._require_socket()
        while True:
            frame = await socket.recv()
            if isinstance(frame, bytes):
                raise PlaybackProtocolError("audio outside an answer")
            message = _control(frame)
            kind = message.get("type")
            if kind == "session_end":
                self.ended = True
                logger.info("playback session ended")
                return
            if kind != "answer_start" or message.get("sample_rate") != SAMPLE_RATE:
                raise PlaybackProtocolError("expected answer_start")
            await self._play_answer(socket, message.get("answer_id"))

    async def _play_answer(self, socket: TransportSocket, answer_id: object) -> None:
        queue: asyncio.Queue[object] = asyncio.Queue()

        async def chunks() -> AsyncIterator[bytes]:
            while True:
                item = await queue.get()
                if item is _END:
                    return
                yield item  # type: ignore[misc]

        playing = asyncio.create_task(self._player.play(chunks()), name="playback-answer")
        try:
            while True:
                frame = await socket.recv()
                if isinstance(frame, bytes):
                    queue.put_nowait(frame)
                    continue
                message = _control(frame)
                if message.get("answer_id") != answer_id or message.get("type") not in ("answer_end", "answer_error"):
                    raise PlaybackProtocolError("unexpected frame inside an answer")
                if message["type"] == "answer_error":
                    logger.warning("answer synthesis failed on the backend")
                break
        finally:
            # Play whatever arrived, including after an error; never cut it short.
            queue.put_nowait(_END)
            await playing
        self.answers_played += 1

    async def _close(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is None:
            return
        try:
            await socket.close()
        except Exception:  # noqa: BLE001
            logger.warning("playback socket close failed")

    def _require_socket(self) -> TransportSocket:
        if self._socket is None:
            raise PlaybackProtocolError("playback socket is not open")
        return self._socket


def _control(frame: str | bytes) -> dict[str, object]:
    if not isinstance(frame, str):
        raise PlaybackProtocolError("expected a text frame")
    try:
        body = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise PlaybackProtocolError("frame is not json") from exc
    if not isinstance(body, dict):
        raise PlaybackProtocolError("frame is not an object")
    return body
