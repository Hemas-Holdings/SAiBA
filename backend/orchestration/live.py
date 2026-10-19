"""Connect one authenticated audio session to recognition and the transcript.

Final utterances are appended while the session is running. Pause stops
recognition and records a gap instead of speech. A certain phrase freezes
the question slice and does not stop recognition. When an orchestrator is
set, each frozen slice is answered in the background and the answer is
appended as an assistant turn while meeting text keeps arriving. Audio and
transcript text are not written to disk or logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from orchestration.question import extract_question
from stt.phrase_detector import PhraseDetector, PhraseHit
from stt.speech import SpeechToText, TranscriptStream
from stt.transcript import SessionTranscript

logger = logging.getLogger(__name__)

AnswerHook = Callable[[str], Awaitable[None]]


class Answerer(Protocol):
    async def answer(self, transcript_slice: str, question: str) -> str: ...


class LiveSession:
    """Server-side meeting for one session id."""

    def __init__(
        self,
        speech: SpeechToText,
        transcript: SessionTranscript,
        detector: PhraseDetector,
        orchestrator: Answerer | None = None,
        on_answer: AnswerHook | None = None,
        answer_timeout: float = 60.0,
    ) -> None:
        self._speech = speech
        self._transcript = transcript
        self._detector = detector
        self._orchestrator = orchestrator
        self._on_answer = on_answer
        self._answer_timeout = answer_timeout
        self._pause_open = False
        self._reader: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._answers: set[asyncio.Task[None]] = set()
        self.speech = speech
        self.transcript = transcript

    async def start(self, session_id: str) -> None:
        del session_id
        self._loop = asyncio.get_running_loop()
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
        await self._finish_answers()
        logger.info("live session stopped")

    async def _finish_answers(self) -> None:
        # Let tasks scheduled from a recognizer thread start before waiting.
        await asyncio.sleep(0)
        pending = set(self._answers)
        if not pending:
            return
        _done, late = await asyncio.wait(pending, timeout=self._answer_timeout)
        for task in late:
            task.cancel()
        if late:
            await asyncio.gather(*late, return_exceptions=True)
            logger.warning("live session cancelled unfinished answers count=%d", len(late))

    async def _drain(self, stream: TranscriptStream) -> None:
        async for _text in stream:
            pass

    def _on_final(self, text: str) -> None:
        if self._transcript.is_paused:
            return
        hit = self._detector.observe(text)
        frozen = self._transcript.observe_utterance(text, hit)
        logger.info("final utterance observed certain=%s", hit is not None)
        if frozen is None or self._orchestrator is None or self._loop is None:
            return
        transcript_slice = self._transcript.slice_through_question(frozen)
        if transcript_slice is None:
            return
        # Azure delivers finals on its own thread; answer on the session loop.
        self._loop.call_soon_threadsafe(self._schedule_answer, transcript_slice, frozen)

    def _schedule_answer(self, transcript_slice: str, hit: PhraseHit) -> None:
        task = asyncio.create_task(self._answer(transcript_slice, hit), name="live-answer")
        self._answers.add(task)
        task.add_done_callback(self._answers.discard)

    async def _answer(self, transcript_slice: str, hit: PhraseHit) -> None:
        question = extract_question(transcript_slice, hit)
        if question is None:
            logger.info("phrase had no question")
            return
        assert self._orchestrator is not None
        answer = await self._orchestrator.answer(transcript_slice, question)
        if not answer:
            return
        if self._transcript.is_paused:
            # The transcript refuses turns during a pause; never deliver
            # an answer that is not in the transcript.
            logger.info("answer dropped because the session is paused")
            return
        self._transcript.append_assistant(answer)
        logger.info("assistant turn appended")
        if self._on_answer is not None:
            try:
                await self._on_answer(answer)
            except Exception as error:  # noqa: BLE001 - delivery never removes the turn
                logger.warning("answer delivery failed error=%s", type(error).__name__)
