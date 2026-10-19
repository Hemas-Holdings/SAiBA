"""Deliver spoken answers to a session's playback socket.

Each session has one channel: a bounded queue of answer text already on
the transcript, and at most one subscriber (the desktop playback socket).
Answers are synthesized and sent one at a time, in order. Publishing never
waits for speech. Stop drains the queue: every pending answer is still
delivered, then `session_end` is sent and the socket is closed. A TTS
failure sends `answer_error`; the transcript keeps the answer. Text and
audio are not written to disk or logged. See docs/session-model.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from tts.speech import SynthesisFailed

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
_DRAIN = object()


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class Subscriber(Protocol):
    """One playback socket. Implementations must not log payloads."""

    async def send(self, data: str | bytes) -> None: ...

    async def close(self) -> None: ...


class SubscriberGone(Exception):
    """The playback socket went away mid-answer."""


class _Channel:
    def __init__(self, max_pending: int) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max_pending + 1)
        self.max_pending = max_pending
        self.subscriber: Subscriber | None = None
        self.attached = asyncio.Event()
        self.draining = False
        self.task: asyncio.Task[None] | None = None
        self.done = asyncio.Event()


class PlaybackHub:
    """Per-session answer delivery shared by the capture and playback paths."""

    def __init__(
        self,
        tts: Synthesizer,
        max_pending: int = 8,
        drain_timeout: float = 90.0,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self._tts = tts
        self._max_pending = max_pending
        self._drain_timeout = drain_timeout
        self._channels: dict[str, _Channel] = {}
        self._watchdogs: set[asyncio.Task[None]] = set()

    def is_open(self, session_id: str) -> bool:
        return session_id in self._channels

    def open(self, session_id: str) -> None:
        """Create the channel for a capture session that has just started."""

        if session_id in self._channels:
            return
        channel = _Channel(self._max_pending)
        channel.task = asyncio.create_task(self._deliver(session_id, channel), name="playback-deliver")
        self._channels[session_id] = channel
        logger.info("playback channel opened")

    def publish(self, session_id: str, text: str) -> bool:
        """Queue one answer for speech. Never waits. False when it was dropped."""

        channel = self._channels.get(session_id)
        if channel is None or channel.draining or not text.strip():
            logger.info("playback answer dropped reason=no_channel")
            return False
        if channel.queue.qsize() >= channel.max_pending:
            logger.warning("playback answer dropped reason=queue_full")
            return False
        channel.queue.put_nowait(text)
        return True

    def close(self, session_id: str) -> None:
        """Finish the queued answers, then end the session's playback."""

        channel = self._channels.get(session_id)
        if channel is None or channel.draining:
            return
        channel.draining = True
        channel.queue.put_nowait(_DRAIN)
        watchdog = asyncio.create_task(self._watch(session_id, channel), name="playback-drain")
        self._watchdogs.add(watchdog)
        watchdog.add_done_callback(self._watchdogs.discard)

    def attach(self, session_id: str, subscriber: Subscriber) -> bool:
        """Bind the playback socket. Refused for unknown sessions or a second socket."""

        channel = self._channels.get(session_id)
        if channel is None or channel.subscriber is not None:
            return False
        channel.subscriber = subscriber
        channel.attached.set()
        logger.info("playback subscriber attached")
        return True

    def detach(self, session_id: str, subscriber: Subscriber) -> None:
        channel = self._channels.get(session_id)
        if channel is None or channel.subscriber is not subscriber:
            return
        channel.subscriber = None
        channel.attached.clear()
        logger.info("playback subscriber detached")

    async def wait_closed(self, session_id: str) -> None:
        """Return once the channel has finished (drained or timed out)."""

        channel = self._channels.get(session_id)
        if channel is not None:
            await channel.done.wait()

    async def shutdown(self) -> None:
        for session_id in list(self._channels):
            self.close(session_id)
        if self._watchdogs:
            await asyncio.gather(*set(self._watchdogs), return_exceptions=True)

    async def _watch(self, session_id: str, channel: _Channel) -> None:
        task = channel.task
        assert task is not None
        _done, pending = await asyncio.wait({task}, timeout=self._drain_timeout)
        if pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            logger.warning("playback drain timed out")
        await self._finish(session_id, channel)

    async def _finish(self, session_id: str, channel: _Channel) -> None:
        subscriber = channel.subscriber
        channel.subscriber = None
        if subscriber is not None:
            try:
                await subscriber.close()
            except Exception:  # noqa: BLE001
                logger.warning("playback subscriber close failed")
        if self._channels.get(session_id) is channel:
            del self._channels[session_id]
        channel.done.set()
        logger.info("playback channel closed")

    async def _deliver(self, session_id: str, channel: _Channel) -> None:
        answer_id = 0
        while True:
            item = await channel.queue.get()
            if item is _DRAIN:
                break
            answer_id += 1
            await channel.attached.wait()
            subscriber = channel.subscriber
            if subscriber is None:
                continue
            try:
                await self._speak(subscriber, answer_id, str(item))
            except SubscriberGone:
                self.detach(session_id, subscriber)
                logger.warning("playback subscriber lost mid-answer")
        subscriber = channel.subscriber
        if subscriber is not None:
            try:
                await subscriber.send(_frame({"type": "session_end"}))
            except Exception:  # noqa: BLE001
                logger.warning("playback session_end not delivered")

    async def _speak(self, subscriber: Subscriber, answer_id: int, text: str) -> None:
        await _send(subscriber, _frame({"type": "answer_start", "answer_id": answer_id, "sample_rate": SAMPLE_RATE}))
        try:
            async for chunk in self._tts.synthesize(text):
                await _send(subscriber, chunk)
        except SynthesisFailed:
            await _send(subscriber, _frame({"type": "answer_error", "answer_id": answer_id}))
            return
        await _send(subscriber, _frame({"type": "answer_end", "answer_id": answer_id}))
        logger.info("playback answer delivered")


async def _send(subscriber: Subscriber, data: str | bytes) -> None:
    try:
        await subscriber.send(data)
    except Exception as error:  # noqa: BLE001
        raise SubscriberGone() from error


def _frame(body: dict[str, object]) -> str:
    return json.dumps(body, separators=(",", ":"))
