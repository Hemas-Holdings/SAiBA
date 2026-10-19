"""Gate: unpaused speech is transcribed, pause is a gap, the slice does not stop STT."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from orchestration.live import LiveSession
from stt.phrase_detector import PhraseDetector, PhraseHit
from stt.speech import SpeechToText
from stt.transcript import SessionTranscript
from tests.test_speech import MemoryAdapter

_T1 = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
_T2 = datetime(2026, 1, 1, 14, 11, tzinfo=timezone.utc)
_HIT = PhraseHit("board assistant")


def _live() -> tuple[LiveSession, MemoryAdapter]:
    adapter = MemoryAdapter()
    speech = SpeechToText(adapter)
    session = LiveSession(speech, SessionTranscript(), PhraseDetector("board assistant"))
    return session, adapter


@pytest.mark.asyncio
async def test_pause_omits_speech_and_phrase_slice_does_not_stop_recognition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, adapter = _live()
    with caplog.at_level(logging.DEBUG):
        await session.start("session-1")
        await session.audio(b"\x01\x02")
        adapter.emit("good morning everyone")
        await session.pause(_T1)
        adapter.emit("secret side conversation")
        await session.resume(_T2)
        adapter.emit("board assistant what was revenue")
        frozen = session.transcript.slice_through_question(_HIT)
        adapter.emit("talk after the question")
    rendered = session.transcript.render()
    assert "good morning everyone" in rendered
    assert "secret side conversation" not in rendered
    assert "[paused 14:02\u201314:11]" in rendered
    assert frozen is not None
    assert "what was revenue" in frozen
    assert "talk after the question" not in frozen
    assert "talk after the question" in rendered
    assert adapter.stop_calls == 0
    assert "secret side conversation" not in caplog.text
    assert "what was revenue" not in caplog.text
    await session.stop(_T2)
    assert adapter.stop_calls == 1


@pytest.mark.asyncio
async def test_phrase_at_utterance_end_waits_and_a_gap_does_not_finish_it() -> None:
    session, adapter = _live()
    await session.start("session-1")
    adapter.emit("board assistant")
    assert session.transcript.slice_through_question(_HIT) is None
    await session.pause(_T1)
    adapter.emit("not in the transcript")
    await session.resume(_T2)
    assert session.transcript.slice_through_question(_HIT) is None
    adapter.emit("what was revenue")
    frozen = session.transcript.slice_through_question(_HIT)
    assert frozen is not None
    assert "what was revenue" in frozen
    assert "not in the transcript" not in (frozen or "")
    assert adapter.stop_calls == 0
    await session.stop(_T2)


@pytest.mark.asyncio
async def test_stop_returns_before_a_slow_answer_and_closes_delivery_after_it() -> None:
    import asyncio

    release = asyncio.Event()

    class SlowOrchestrator:
        async def answer(self, transcript_slice: str, question: str) -> str:
            await release.wait()
            return "the answer"

    class Delivery:
        def __init__(self) -> None:
            self.events: list[str] = []

        def open(self, session_id: str) -> None:
            self.events.append(f"open {session_id}")

        def publish(self, session_id: str, text: str) -> bool:
            self.events.append(f"publish {text}")
            return True

        def close(self, session_id: str) -> None:
            self.events.append(f"close {session_id}")

    adapter = MemoryAdapter()
    delivery = Delivery()
    closed: list[str] = []
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("board assistant"),
        orchestrator=SlowOrchestrator(),
        delivery=delivery,
        on_closed=lambda transcript: closed.append(transcript.render()),
    )
    await session.start("session-9")
    adapter.emit("board assistant what was revenue")
    await asyncio.sleep(0)
    await asyncio.wait_for(session.stop(_T2), timeout=0.5)
    assert adapter.stop_calls == 1
    assert delivery.events == ["open session-9"]
    release.set()
    await session.wait_closed()
    assert delivery.events == ["open session-9", "publish the answer", "close session-9"]
    assert "Assistant: the answer" in closed[0]
