"""Connect one authenticated audio session to recognition and the transcript.

Final utterances are appended while the session is running. Pause stops
recognition and records a gap instead of speech. A certain phrase freezes
the question slice and does not stop recognition. Audio and transcript text
are not written to disk or logged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from stt.phrase_detector import PhraseDetector
from stt.speech import SpeechToText, TranscriptStream
from stt.transcript import SessionTranscript

logger = logging.getLogger(__name__)


class LiveSession:
    """Server-side meeting for one session id."""

    def __init__(
        self,
        speech: SpeechToText,
        transcript: SessionTranscript,
        detector: PhraseDetector,
    ) -> None:
        self._speech = speech
        self._transcript = transcript
        self._detector = detector
        self._pause_open = False
        self._reader: asyncio.Task[None] | None = None
        self.speech = speech
        self.transcript = transcript

    async def start(self, session_id: str) -> None:
        del session_id
        self._speech.set_listener(self._on_final)
        stream = await self._speech.start_session()
        self._reader = asyncio.create_task(self._drain(stream), name="live-transcript")
        logger.info("live session recognition started")

    async def audio(self, chunk: bytes) -> None:
        await self._speech.push_audio(chunk)

    async def pause(self, at: datetime) -> None:
        await self._speech.pause()
        self._transcript.begin_pause(at)
        self._pause_open = True
        logger.info("live session paused")

    async def resume(self, at: datetime) -> None:
        if self._pause_open:
            self._transcript.end_pause(at)
            self._pause_open = False
        await self._speech.resume()
        logger.info("live session resumed")

    async def stop(self, at: datetime) -> None:
        if self._pause_open:
            self._transcript.end_pause(at)
            self._pause_open = False
        await self._speech.stop()
        reader = self._reader
        self._reader = None
        if reader is not None:
            await reader
        logger.info("live session stopped")

    async def _drain(self, stream: TranscriptStream) -> None:
        async for _text in stream:
            pass

    def _on_final(self, text: str) -> None:
        if self._transcript.is_paused:
            return
        hit = self._detector.observe(text)
        self._transcript.observe_utterance(text, hit)
        logger.info("final utterance observed certain=%s", hit is not None)
