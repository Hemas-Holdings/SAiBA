"""Gate: governed records hold the running transcript and answers, never paused speech."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from orchestration.live import LiveSession
from storage.board import InteractionRecord, PauseGap, SessionRecord, payload_of
from stt.phrase_detector import PhraseDetector
from stt.speech import SpeechToText
from stt.transcript import SessionTranscript
from tests.test_speech import MemoryAdapter

_START = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
_P1 = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
_P2 = datetime(2026, 10, 26, 9, 9, tzinfo=timezone.utc)
_STOP = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


class MemoryStorage:
    def __init__(self, fail: bool = False) -> None:
        self.interactions: list[InteractionRecord] = []
        self.sessions: list[SessionRecord] = []
        self._fail = fail

    async def log_interaction(self, record: InteractionRecord) -> None:
        payload_of(record)
        if self._fail:
            raise OSError("disk full")
        self.interactions.append(record)

    async def finalize_session(self, record: SessionRecord) -> None:
        payload_of(record)
        if self._fail:
            raise OSError("disk full")
        self.sessions.append(record)


class Orchestrator:
    async def answer(self, transcript_slice: str, question: str) -> str:
        return f"Answer to: {question}"


def _live(
    storage: MemoryStorage,
    orchestrator: object | None = None,
    seen: list[str] | None = None,
) -> tuple[LiveSession, MemoryAdapter]:
    adapter = MemoryAdapter()
    on_closed = (lambda transcript: seen.append(transcript.render())) if seen is not None else None
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("board assistant"),
        orchestrator=orchestrator if orchestrator is not None else Orchestrator(),
        storage=storage,
        clock=lambda: _START,
        on_closed=on_closed,
    )
    return session, adapter


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_interactions_and_session_are_recorded_without_paused_speech(
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = MemoryStorage()
    session, adapter = _live(storage)
    with caplog.at_level(logging.DEBUG):
        await session.start("session-1")
        adapter.emit("good morning everyone")
        await session.pause(_P1)
        adapter.emit("secret side conversation")
        await session.resume(_P2)
        adapter.emit("board assistant what was revenue?")
        await _settle()
        await session.stop(_STOP)
        await session.wait_closed()

    [interaction] = storage.interactions
    assert interaction.session_id == "session-1"
    assert interaction.sequence == 1
    assert interaction.question == "what was revenue?"
    assert "board assistant what was revenue" in interaction.question_slice
    assert interaction.answer == "Answer to: what was revenue?"
    assert "Assistant: Answer to: what was revenue?" in interaction.transcript

    [finished] = storage.sessions
    assert finished.started_at == _START
    assert finished.stopped_at == _STOP
    assert finished.interaction_count == 1
    assert finished.pause_gaps == (PauseGap(_P1, _P2),)
    assert "[paused 09:05–09:09]" in finished.transcript
    assert "Assistant: Answer to: what was revenue?" in finished.transcript

    for text in (interaction.question_slice, interaction.transcript, finished.transcript):
        assert "secret side conversation" not in text
    assert "what was revenue" not in caplog.text
    assert "good morning" not in caplog.text


async def test_transcript_is_cleared_from_memory_after_the_final_write() -> None:
    storage = MemoryStorage()
    seen: list[str] = []
    session, adapter = _live(storage, seen=seen)
    await session.start("session-1")
    adapter.emit("good morning everyone")
    await session.stop(_STOP)
    await session.wait_closed()
    assert storage.sessions[0].transcript == "good morning everyone"
    assert seen == ["good morning everyone"]
    assert session.transcript.render() == ""
    assert session.transcript.pause_gaps() == []


async def test_session_without_questions_is_still_finalized() -> None:
    storage = MemoryStorage()
    session, adapter = _live(storage)
    await session.start("session-2")
    adapter.emit("just a normal discussion")
    await session.stop(_STOP)
    await session.wait_closed()
    assert storage.interactions == []
    assert storage.sessions[0].interaction_count == 0


async def test_pause_left_open_at_stop_is_closed_and_recorded() -> None:
    storage = MemoryStorage()
    session, _adapter = _live(storage)
    await session.start("session-3")
    await session.pause(_P1)
    await session.stop(_STOP)
    await session.wait_closed()
    assert storage.sessions[0].pause_gaps == (PauseGap(_P1, _STOP),)


async def test_answer_dropped_during_pause_is_not_stored() -> None:
    release = asyncio.Event()

    class SlowOrchestrator:
        async def answer(self, transcript_slice: str, question: str) -> str:
            await release.wait()
            return "late answer"

    storage = MemoryStorage()
    session, adapter = _live(storage, SlowOrchestrator())
    await session.start("session-4")
    adapter.emit("board assistant what was revenue?")
    await _settle()
    await session.pause(_P1)
    release.set()
    await _settle()
    await session.stop(_STOP)
    await session.wait_closed()
    assert storage.interactions == []
    assert "late answer" not in storage.sessions[0].transcript


async def test_storage_failure_keeps_the_answer_and_still_clears_memory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = MemoryStorage(fail=True)
    seen: list[str] = []
    session, adapter = _live(storage, seen=seen)
    with caplog.at_level(logging.WARNING):
        await session.start("session-5")
        adapter.emit("board assistant what was revenue?")
        await _settle()
        await session.stop(_STOP)
        await session.wait_closed()
    assert "Assistant: Answer to: what was revenue?" in seen[0]
    assert "interaction storage failed error=OSError" in caplog.text
    assert "session storage failed error=OSError" in caplog.text
    assert "disk full" not in caplog.text
    assert session.transcript.render() == ""


async def test_no_storage_means_nothing_is_recorded_and_sessions_still_work() -> None:
    adapter = MemoryAdapter()
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("board assistant"),
        orchestrator=Orchestrator(),
    )
    await session.start("session-6")
    adapter.emit("board assistant what was revenue?")
    await _settle()
    assert "Assistant: Answer to: what was revenue?" in session.transcript.render()
    await session.stop(_STOP)
    await session.wait_closed()
