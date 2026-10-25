"""Forward local mixed PCM to the backend while a session is running.

Listens to the local session controller. Pause and Stop stay local actions
on that controller; this module only reports the resulting state. A transport
failure stops forwarding and does not drain remaining audio. While an answer
is playing (`muted`), silence of the same length is sent instead of the mix,
so the answer is not transcribed back as meeting speech. The playback
client runs beside capture: Pause, Resume and Stop do not cut it. This
module does not write audio to disk or log samples.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from capture.audio_format import SAMPLE_WIDTH, silence
from session.controller import SessionState
from transport.client import BackendClient, TransportError

logger = logging.getLogger(__name__)


class LocalSession(Protocol):
    """The local capture session. Start / Pause / Resume / Stop are not remote."""

    @property
    def session_id(self) -> str | None:
        """Current session id, or None when idle."""

    async def start(self) -> SessionState:
        """Begin local capture."""

    async def pause(self) -> SessionState:
        """Stop local capture and keep the session."""

    async def resume(self) -> SessionState:
        """Continue local capture on the same session."""

    async def stop(self) -> SessionState:
        """End local capture."""

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield mixed PCM only while the session is running."""


class PlaybackRunner(Protocol):
    async def run(self, session_id: str) -> None:
        """Play the session's answers until the backend ends playback."""


class SessionStreamer:
    """Tie a local session to a backend client and, optionally, playback."""

    def __init__(
        self,
        controller: LocalSession,
        client: BackendClient,
        playback: PlaybackRunner | None = None,
        muted: Callable[[], bool] | None = None,
        playback_grace: float = 90.0,
    ) -> None:
        self._controller = controller
        self._client = client
        self._playback = playback
        self._muted = muted
        self._playback_grace = playback_grace
        self._forward: asyncio.Task[None] | None = None
        self._playback_task: asyncio.Task[None] | None = None

    @property
    def forwarding(self) -> bool:
        """True while audio is being forwarded. False once the link has failed or after Stop."""

        return self._forward is not None and not self._forward.done()

    async def start(self) -> SessionState:
        """Start local capture, then the remote session. Capture stops if the link fails."""

        state = await self._controller.start()
        session_id = self._controller.session_id
        if session_id is None:
            await self._controller.stop()
            raise TransportError("local session did not produce an id")
        try:
            await self._client.start_session(session_id)
        except TransportError:
            logger.warning("transport start failed closed")
            await self._controller.pause()
            raise
        self._forward = asyncio.create_task(self._forward_audio(), name="session-stream")
        if self._playback is not None:
            self._playback_task = asyncio.create_task(
                self._playback.run(session_id), name="session-playback"
            )
        return state

    async def pause(self) -> SessionState:
        """Cut the wire first, then pause local capture."""

        await self._client.pause_session()
        return await self._controller.pause()

    async def resume(self) -> SessionState:
        """Resume local capture, then allow audio on the wire again."""

        state = await self._controller.resume()
        await self._client.resume_session()
        return state

    async def stop(self) -> SessionState:
        """End local capture and the remote session."""

        state = await self._controller.stop()
        await self._finish_forward()
        await self._client.stop_session()
        return state

    async def wait_playback(self) -> None:
        """After Stop: let answers still playing finish, up to the grace period."""

        task = self._playback_task
        self._playback_task = None
        if task is None:
            return
        _done, pending = await asyncio.wait({task}, timeout=self._playback_grace)
        for late in pending:
            late.cancel()
            logger.warning("playback did not end in time; cancelled")
        await asyncio.gather(task, return_exceptions=True)

    async def _forward_audio(self) -> None:
        chunks = self._controller.chunks()
        try:
            async for chunk in chunks:
                if self._muted is not None and self._muted():
                    # An answer is playing: keep the stream's clock, drop the echo.
                    chunk = silence(len(chunk) // SAMPLE_WIDTH)
                try:
                    await self._client.send_audio(chunk)
                except TransportError:
                    logger.warning("transport forward failed closed")
                    return
        finally:
            await chunks.aclose()

    async def _finish_forward(self) -> None:
        task = self._forward
        self._forward = None
        if task is None:
            return
        try:
            await task
        except Exception:
            logger.warning("transport forward ended")
