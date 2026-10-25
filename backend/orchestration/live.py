"""Connect one authenticated audio session to recognition and the transcript.

Final utterances are appended while the session is running. Pause stops
recognition and records a gap instead of speech. A certain phrase freezes
the question slice and does not stop recognition. When an orchestrator is
set, each frozen slice is answered in the background and the answer is
appended as an assistant turn while meeting text keeps arriving, then
handed to delivery for speech. Stop ends recognition at once; answers
already being prepared still finish and are delivered before the session's
delivery channel closes. When governed storage is set, each answered
question is recorded after its answer is on the transcript, and at Stop the
finished session is recorded once every answer is done; the transcript is
then cleared from memory. Storage is the only path that persists text and
never receives audio. Nothing here logs transcript text.

When window events are set, the desktop window is told about question
progress: a trigger cue, the question as it is spoken, the final question,
and the answer. Only text after the trigger phrase is ever sent, never the
rest of the meeting. The cue may come from in-progress recognition, so the
window reacts at once; analysis still runs only on a certain phrase in a
final utterance, and a cue the final does not confirm is cancelled.

A question that does not end in "?" is held briefly, because people pause
mid-question and recognition splits it: if speech resumes within the grace
period, the next utterance joins the question; otherwise it is answered as
it stands. In-progress recognition is used only to tell that speech resumed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from orchestration.question import extract_question, text_after_phrase
from stt.phrase_detector import PhraseDetector, PhraseHit
from stt.speech import SpeechToText, TranscriptStream
from stt.transcript import SessionTranscript
from storage.board import BoardStorage, InteractionRecord, PauseGap, SessionRecord

logger = logging.getLogger(__name__)

class Answerer(Protocol):
    async def answer(self, transcript_slice: str, question: str) -> str: ...


class AnswerDelivery(Protocol):
    """Speaks answers that are already on the transcript. Must not block."""

    def open(self, session_id: str) -> None: ...

    def publish(self, session_id: str, text: str) -> bool: ...

    def close(self, session_id: str) -> None: ...


class SessionEvents(Protocol):
    """Shows question progress in the desktop window. Must not block or log text."""

    def notify(self, session_id: str, event: dict[str, object]) -> None: ...


# Display limits for text sent to the window.
_MAX_QUESTION_CHARS = 1000
_MAX_ANSWER_CHARS = 4000


class LiveSession:
    """Server-side meeting for one session id."""

    def __init__(
        self,
        speech: SpeechToText,
        transcript: SessionTranscript,
        detector: PhraseDetector,
        orchestrator: Answerer | None = None,
        delivery: AnswerDelivery | None = None,
        answer_timeout: float = 60.0,
        on_closed: Callable[[SessionTranscript], None] | None = None,
        storage: BoardStorage | None = None,
        clock: Callable[[], datetime] | None = None,
        events: SessionEvents | None = None,
        question_grace: float = 1.5,
        continuation_timeout: float = 8.0,
    ) -> None:
        self._speech = speech
        self._transcript = transcript
        self._detector = detector
        self._orchestrator = orchestrator
        self._delivery = delivery
        self._answer_timeout = answer_timeout
        self._on_closed = on_closed
        self._storage = storage
        self._clock = clock if clock is not None else _utc_now
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._interactions = 0
        self._session_id: str | None = None
        self._closing: asyncio.Task[None] | None = None
        self._pause_open = False
        self._reader: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._answers: set[asyncio.Task[None]] = set()
        self._events = events
        # Window cue state. Touched only on the session loop.
        self._cue = False
        self._awaiting_question = False
        self._question_id = 0
        # Held-question timer (session loop only) and the lock that keeps the
        # recognizer thread and the timer from freezing the same question twice.
        self._question_grace = question_grace
        self._continuation_timeout = continuation_timeout
        self._hold_timer: asyncio.TimerHandle | None = None
        self._freeze_lock = threading.Lock()
        self.speech = speech
        self.transcript = transcript

    async def start(self, session_id: str) -> None:
        self._session_id = session_id
        self._started_at = self._clock()
        self._loop = asyncio.get_running_loop()
        if self._delivery is not None:
            self._delivery.open(session_id)
        self._speech.set_listener(self._on_final)
        # In-progress text drives the window cue and tells a held question that
        # speech resumed. It never enters the transcript or analysis.
        self._speech.set_partial_listener(self._on_partial)
        stream = await self._speech.start_session()
        self._reader = asyncio.create_task(self._drain(stream), name="live-transcript")
        logger.info("live session recognition started")

    async def audio(self, chunk: bytes) -> None:
        await self._speech.push_audio(chunk)

    async def pause(self, at: datetime) -> None:
        await self._speech.pause()
        self._transcript.begin_pause(at)
        self._pause_open = True
        # A held question stays held across a pause; its timer restarts on Resume.
        self._cancel_hold()
        if self._cue and not self._awaiting_question:
            self._cue = False
            self._emit({"type": "trigger_cancelled", "question_id": self._question_id})
        logger.info("live session paused")

    async def resume(self, at: datetime) -> None:
        if self._pause_open:
            self._transcript.end_pause(at)
            self._pause_open = False
        await self._speech.resume()
        if self._transcript.question_may_continue:
            self._arm_hold(self._question_grace)
        logger.info("live session resumed")

    async def stop(self, at: datetime) -> None:
        if self._pause_open:
            self._transcript.end_pause(at)
            self._pause_open = False
        self._stopped_at = at
        await self._speech.stop()
        reader = self._reader
        self._reader = None
        if reader is not None:
            await reader
        # A question still held at Stop is answered as it stands.
        self._cancel_hold()
        self._close_held()
        # Do not hold up the Stop reply: answers finish in the background.
        self._closing = asyncio.create_task(self._close_after_answers(), name="live-close")
        logger.info("live session stopped")

    async def wait_closed(self) -> None:
        """Return once answers in flight at Stop are done and delivery is closed."""

        closing = self._closing
        if closing is not None:
            await closing

    async def _close_after_answers(self) -> None:
        await self._finish_answers()
        if self._delivery is not None and self._session_id is not None:
            self._delivery.close(self._session_id)
        await self._store_session()
        if self._on_closed is not None:
            self._on_closed(self._transcript)
        # Stop: the governed write is done, so nothing stays in memory.
        self._transcript.clear()
        logger.info("live session closed")

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
        with self._freeze_lock:
            hit = self._detector.observe(text)
            frozen = self._transcript.observe_utterance(
                text, hit, hold_unfinished=self._orchestrator is not None
            )
            held = self._transcript.question_may_continue
        logger.info("final utterance observed certain=%s", hit is not None)
        if self._loop is None:
            return
        if self._events is not None:
            self._loop.call_soon_threadsafe(self._final_cue, hit is not None, frozen is not None)
        if held and frozen is None:
            # Unfinished question: wait briefly for the rest of it.
            self._loop.call_soon_threadsafe(self._arm_hold, self._question_grace)
        if frozen is None or self._orchestrator is None:
            return
        self._loop.call_soon_threadsafe(self._cancel_hold)
        transcript_slice = self._transcript.slice_through_question(frozen)
        if transcript_slice is None:
            return
        # Azure delivers finals on its own thread; answer on the session loop.
        self._loop.call_soon_threadsafe(self._schedule_answer, transcript_slice, frozen)

    def _on_partial(self, text: str) -> None:
        # Azure delivers partials on its own thread; handle them on the session loop.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._partial_on_loop, text)

    def _partial_on_loop(self, text: str) -> None:
        if self._hold_timer is not None and not self._transcript.is_paused:
            # Speech resumed: the next final joins the held question. Keep a
            # longer fallback in case that speech never becomes a final.
            self._arm_hold(self._continuation_timeout)
        if self._events is not None:
            self._partial_cue(text)

    def _arm_hold(self, delay: float) -> None:
        self._cancel_hold()
        if self._loop is not None:
            self._hold_timer = self._loop.call_later(delay, self._hold_expired)

    def _cancel_hold(self) -> None:
        timer = self._hold_timer
        self._hold_timer = None
        if timer is not None:
            timer.cancel()

    def _hold_expired(self) -> None:
        self._hold_timer = None
        self._close_held()

    def _close_held(self) -> None:
        """Answer a held question as it stands."""

        with self._freeze_lock:
            hit = self._transcript.close_held_question()
            transcript_slice = self._transcript.slice_through_question(hit) if hit is not None else None
        if hit is None or transcript_slice is None or self._orchestrator is None:
            return
        self._cue = False
        self._awaiting_question = False
        self._schedule_answer(transcript_slice, hit)

    def _partial_cue(self, text: str) -> None:
        """Window cue only. In-progress text never reaches the transcript or analysis."""

        if self._transcript.is_paused:
            return
        if self._awaiting_question:
            shown: str | None = text
        else:
            shown = text_after_phrase(text, self._detector.phrase)
            if shown is None:
                return
        self._raise_cue()
        if shown:
            self._emit(
                {"type": "question_partial", "question_id": self._question_id, "text": shown[:_MAX_QUESTION_CHARS]}
            )

    def _final_cue(self, certain: bool, frozen: bool) -> None:
        if certain:
            self._raise_cue()
        if frozen:
            # The question is complete; `_answer` reports it.
            self._cue = False
            self._awaiting_question = False
        elif certain:
            # The phrase ended the utterance: the next one is the question.
            self._awaiting_question = True
        elif self._cue and not self._awaiting_question:
            # In-progress text looked like the phrase; the final did not confirm it.
            self._cue = False
            self._emit({"type": "trigger_cancelled", "question_id": self._question_id})

    def _raise_cue(self) -> None:
        if self._cue:
            return
        self._cue = True
        self._question_id += 1
        self._emit({"type": "trigger", "question_id": self._question_id})

    def _emit(self, event: dict[str, object]) -> None:
        if self._events is None or self._session_id is None:
            return
        try:
            self._events.notify(self._session_id, event)
        except Exception as error:  # noqa: BLE001 - the window is never load-bearing
            logger.warning("window event failed error=%s", type(error).__name__)

    def _schedule_answer(self, transcript_slice: str, hit: PhraseHit) -> None:
        question_id = self._question_id
        task = asyncio.create_task(self._answer(transcript_slice, hit, question_id), name="live-answer")
        self._answers.add(task)
        task.add_done_callback(self._answers.discard)

    async def _answer(self, transcript_slice: str, hit: PhraseHit, question_id: int = 0) -> None:
        question = extract_question(transcript_slice, hit)
        if question is None:
            logger.info("phrase had no question")
            self._emit({"type": "no_question", "question_id": question_id})
            return
        self._emit({"type": "question", "question_id": question_id, "text": question[:_MAX_QUESTION_CHARS]})
        assert self._orchestrator is not None
        answer = await self._orchestrator.answer(transcript_slice, question)
        if not answer:
            self._emit({"type": "answer_unavailable", "question_id": question_id})
            return
        if self._transcript.is_paused:
            # The transcript refuses turns during a pause; never deliver
            # an answer that is not in the transcript.
            logger.info("answer dropped because the session is paused")
            self._emit({"type": "answer_unavailable", "question_id": question_id})
            return
        self._transcript.append_assistant(answer)
        logger.info("assistant turn appended")
        self._emit({"type": "answer_text", "question_id": question_id, "text": answer[:_MAX_ANSWER_CHARS]})
        if self._delivery is not None and self._session_id is not None:
            try:
                self._delivery.publish(self._session_id, answer)
            except Exception as error:  # noqa: BLE001 - delivery never removes the turn
                logger.warning("answer delivery failed error=%s", type(error).__name__)
        await self._store_interaction(question, transcript_slice, answer)

    async def _store_interaction(self, question: str, transcript_slice: str, answer: str) -> None:
        if self._storage is None or self._session_id is None:
            return
        self._interactions += 1
        record = InteractionRecord(
            session_id=self._session_id,
            sequence=self._interactions,
            answered_at=self._clock(),
            question=question,
            question_slice=transcript_slice,
            answer=answer,
            transcript=self._transcript.render(),
        )
        try:
            await self._storage.log_interaction(record)
        except Exception as error:  # noqa: BLE001 - the transcript keeps the answer
            logger.warning("interaction storage failed error=%s", type(error).__name__)

    async def _store_session(self) -> None:
        if self._storage is None or self._session_id is None or self._started_at is None:
            return
        record = SessionRecord(
            session_id=self._session_id,
            started_at=self._started_at,
            stopped_at=self._stopped_at or self._clock(),
            interaction_count=self._interactions,
            pause_gaps=tuple(PauseGap(start, end) for start, end in self._transcript.pause_gaps()),
            transcript=self._transcript.render(),
        )
        try:
            await self._storage.finalize_session(record)
        except Exception as error:  # noqa: BLE001 - report; memory is still cleared
            logger.warning("session storage failed error=%s", type(error).__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
