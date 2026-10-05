"""Local Start / Pause / Resume / Stop for one capture session.

Drives loopback and microphone capture, mixes them, and writes the mixed
PCM into an in-memory buffer. Callers can iterate `chunks()` only while the
session is running. Pause closes the devices and discards buffered audio.
Resume continues the same session id. Stop tears the session down.

These actions are local. This module does not import a network client and
does not look at transcript text. Audio bytes are not logged and are not
written to disk. See docs/session-model.md.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from enum import Enum

from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from capture.mixer import AudioMixer
from session.buffer import BufferRefused, StreamBuffer

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Local session lifecycle. Transitions are only the four user actions."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class InvalidSessionTransition(Exception):
    """Raised when Start, Pause, Resume, or Stop is not legal in the current state."""

    def __init__(self, state: SessionState, action: str) -> None:
        self.state = state
        self.action = action
        super().__init__(f"cannot {action} from {state.value}")


class SessionController:
    """Own one local capture session and its in-memory mixed stream."""

    def __init__(
        self,
        loopback: LoopbackCapture,
        microphone: MicCapture,
        mixer: AudioMixer,
        buffer: StreamBuffer,
    ) -> None:
        self._loopback = loopback
        self._microphone = microphone
        self._mixer = mixer
        self._buffer = buffer
        self._state = SessionState.IDLE
        self._session_id: str | None = None
        self._condition = asyncio.Condition()
        self._pump_task: asyncio.Task[None] | None = None
        self._opened = asyncio.Event()
        self._pump_error: BaseException | None = None
        self._arming = False
        self._epoch = 0

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> str | None:
        """Id for the current session. Unchanged across Pause and Resume."""

        return self._session_id

    async def start(self) -> SessionState:
        """Open capture and begin a new session. Legal only from idle."""

        async with self._condition:
            if self._state is not SessionState.IDLE:
                raise InvalidSessionTransition(self._state, "start")
            self._session_id = str(uuid.uuid4())
            self._pump_error = None
            self._opened = asyncio.Event()
            self._arming = True
            self._buffer.accept()
            self._state = SessionState.RUNNING
            self._pump_task = asyncio.create_task(self._pump(), name="session-capture")
            self._pump_task.add_done_callback(self._on_pump_done)
            self._condition.notify_all()
        await self._opened.wait()
        if await self._startup_failed():
            error = self._pump_error
            await self._abort_to_idle()
            raise error if error is not None else RuntimeError("session capture pump failed")
        logger.info("session started")
        return self._state

    async def pause(self) -> SessionState:
        """Stop capture, discard buffered PCM, and keep the session id."""

        async with self._condition:
            if self._state is not SessionState.RUNNING:
                raise InvalidSessionTransition(self._state, "pause")
            self._state = SessionState.PAUSED
            self._epoch += 1
            self._buffer.refuse()
            self._condition.notify_all()
        self._loopback.stop()
        self._microphone.stop()
        await self._await_pump()
        async with self._condition:
            self._buffer.clear()
            self._pump_task = None
            self._condition.notify_all()
        logger.info("session paused")
        return self._state

    async def resume(self) -> SessionState:
        """Reopen capture on the same session. Legal only from paused."""

        async with self._condition:
            if self._state is not SessionState.PAUSED:
                raise InvalidSessionTransition(self._state, "resume")
            self._pump_error = None
            self._opened = asyncio.Event()
            self._arming = True
            self._buffer.accept()
            self._state = SessionState.RUNNING
            self._pump_task = asyncio.create_task(self._pump(), name="session-capture")
            self._pump_task.add_done_callback(self._on_pump_done)
            self._condition.notify_all()
        await self._opened.wait()
        if await self._startup_failed():
            error = self._pump_error
            await self._rollback_resume()
            raise error if error is not None else RuntimeError("session capture pump failed")
        logger.info("session resumed")
        return self._state

    async def stop(self) -> SessionState:
        """Release devices, discard buffered PCM, and end the session."""

        async with self._condition:
            if self._state is SessionState.IDLE:
                raise InvalidSessionTransition(self._state, "stop")
            was_running = self._state is SessionState.RUNNING
            self._state = SessionState.IDLE
            self._epoch += 1
            self._buffer.refuse()
            self._condition.notify_all()
        self._loopback.stop()
        self._microphone.stop()
        if was_running:
            await self._await_pump()
        async with self._condition:
            self._buffer.clear()
            self._session_id = None
            self._pump_task = None
            self._condition.notify_all()
        logger.info("session stopped")
        return self._state

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield mixed PCM captured while running.

        The iterator waits through Pause and ends on Stop. Audio taken from
        the buffer after Pause or Stop has begun is discarded, not yielded.
        """

        while True:
            async with self._condition:
                while True:
                    if self._state is SessionState.IDLE and not self._arming:
                        return
                    if self._state is SessionState.RUNNING:
                        data = self._buffer.read()
                        if data:
                            epoch = self._epoch
                            break
                    await self._condition.wait()
            async with self._condition:
                if self._state is not SessionState.RUNNING or epoch != self._epoch:
                    continue
            yield data

    async def _pump(self) -> None:
        mixed = self._mixer.mix(self._loopback.start(), self._microphone.start())
        try:
            async for chunk in mixed:
                async with self._condition:
                    if self._state is not SessionState.RUNNING:
                        break
                    try:
                        self._buffer.write(chunk)
                    except BufferRefused:
                        break
                    if not self._opened.is_set():
                        self._opened.set()
                    self._condition.notify_all()
        except Exception as exc:
            self._pump_error = exc
            logger.warning("session capture pump failed")
            raise
        finally:
            self._loopback.stop()
            self._microphone.stop()
            try:
                await mixed.aclose()
            except RuntimeError:
                pass
            if not self._opened.is_set():
                self._opened.set()

    def _on_pump_done(self, task: asyncio.Task[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._fail_closed_if_still_running(task))

    async def _fail_closed_if_still_running(self, task: asyncio.Task[None]) -> None:
        """If capture dies while running, pause and drop buffered audio."""

        if not task.cancelled():
            task.exception()
        async with self._condition:
            if self._arming or self._state is not SessionState.RUNNING:
                return
            if task.cancelled():
                return
            self._state = SessionState.PAUSED
            self._epoch += 1
            self._buffer.refuse()
            self._buffer.clear()
            self._pump_task = None
            self._condition.notify_all()
        self._loopback.stop()
        self._microphone.stop()
        logger.warning("session capture failed closed")

    async def _startup_failed(self) -> bool:
        async with self._condition:
            self._arming = False
            task = self._pump_task
            if self._pump_error is not None:
                return True
            if task is not None and task.done() and not task.cancelled():
                return task.exception() is not None
            return False

    async def _abort_to_idle(self) -> None:
        async with self._condition:
            self._state = SessionState.IDLE
            self._arming = False
            self._epoch += 1
            self._buffer.refuse()
            self._condition.notify_all()
        self._loopback.stop()
        self._microphone.stop()
        await self._await_pump()
        async with self._condition:
            self._buffer.clear()
            self._session_id = None
            self._pump_task = None
            self._condition.notify_all()
        logger.warning("session start failed closed")

    async def _rollback_resume(self) -> None:
        async with self._condition:
            self._state = SessionState.PAUSED
            self._arming = False
            self._epoch += 1
            self._buffer.refuse()
            self._condition.notify_all()
        self._loopback.stop()
        self._microphone.stop()
        await self._await_pump()
        async with self._condition:
            self._buffer.clear()
            self._pump_task = None
            self._condition.notify_all()
        logger.warning("session resume failed closed")

    async def _await_pump(self) -> None:
        task = self._pump_task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("session capture pump ended")
