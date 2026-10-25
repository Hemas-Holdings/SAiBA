"""Unfinished questions wait briefly for the rest; Azure is told the names to expect."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orchestration import __main__ as entry
from orchestration.live import LiveSession
from orchestration.question import extract_question
from stt.phrase_detector import PhraseDetector, PhraseHit
from stt.speech import AzureSpeechAdapter, SpeechToText
from stt.transcript import SessionTranscript
from tests.test_speech import FakeSDK, MemoryAdapter

_HIT = PhraseHit("Jenny")
_T1 = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
_T2 = datetime(2026, 10, 26, 9, 9, tzinfo=timezone.utc)

# -------------------------------------------------------------- transcript


def test_unfinished_question_is_held_and_the_next_utterance_joins_it() -> None:
    transcript = SessionTranscript()
    assert transcript.observe_utterance("Jenny, get me Hemas Holdings.", _HIT, hold_unfinished=True) is None
    assert transcript.question_may_continue
    assert transcript.observe_utterance("Total value as of last quarter.", None, hold_unfinished=True) == _HIT
    assert not transcript.question_may_continue
    frozen = transcript.slice_through_question(_HIT)
    assert extract_question(frozen or "", _HIT) == "get me Hemas Holdings. Total value as of last quarter."


def test_a_question_mark_ends_the_question_at_once() -> None:
    transcript = SessionTranscript()
    assert transcript.observe_utterance("Jenny, what was revenue?", _HIT, hold_unfinished=True) == _HIT
    assert not transcript.question_may_continue


def test_held_question_can_be_closed_as_it_stands() -> None:
    transcript = SessionTranscript()
    assert transcript.close_held_question() is None
    transcript.observe_utterance("Jenny, get me the share price.", _HIT, hold_unfinished=True)
    transcript.begin_pause(_T1)
    assert transcript.close_held_question() is None  # never during a pause
    transcript.end_pause(_T2)
    assert transcript.close_held_question() == _HIT
    assert extract_question(transcript.slice_through_question(_HIT) or "", _HIT) == "get me the share price."
    assert transcript.close_held_question() is None


def test_without_hold_the_old_behaviour_is_unchanged() -> None:
    transcript = SessionTranscript()
    assert transcript.observe_utterance("Jenny, get me revenue.", _HIT) == _HIT
    other = SessionTranscript()
    assert other.observe_utterance("Jenny.", _HIT, hold_unfinished=True) is None
    assert not other.question_may_continue  # phrase alone: waits for the next utterance as before
    assert other.observe_utterance("what was revenue", None, hold_unfinished=True) == _HIT


# ------------------------------------------------------------ live session


class Orchestrator:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def answer(self, transcript_slice: str, question: str) -> str:
        self.asked.append(question)
        return f"Answer to: {question}"


def _live(grace: float = 0.05, continuation: float = 0.3) -> tuple[LiveSession, MemoryAdapter, Orchestrator]:
    adapter = MemoryAdapter()
    orchestrator = Orchestrator()
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("Jenny"),
        orchestrator=orchestrator,
        question_grace=grace,
        continuation_timeout=continuation,
    )
    return session, adapter, orchestrator


async def _settle(seconds: float = 0.0) -> None:
    await asyncio.sleep(seconds)
    for _ in range(10):
        await asyncio.sleep(0)


async def test_continuation_joins_the_question() -> None:
    session, adapter, orchestrator = _live(grace=0.5)
    await session.start("s")
    adapter.emit("Jenny, get me Hemas Holdings.")
    await _settle()
    assert orchestrator.asked == []
    adapter.emit("Total value as of last quarter.")
    await _settle()
    assert orchestrator.asked == ["get me Hemas Holdings. Total value as of last quarter."]
    await _settle(0.6)  # the cancelled timer must not ask again
    assert len(orchestrator.asked) == 1
    await session.stop(_T2)


async def test_no_further_speech_answers_the_question_as_it_stands() -> None:
    session, adapter, orchestrator = _live(grace=0.05)
    await session.start("s")
    adapter.emit("Jenny, get me the share price.")
    await _settle()
    assert orchestrator.asked == []
    await _settle(0.1)
    assert orchestrator.asked == ["get me the share price."]
    await session.stop(_T2)


async def test_resumed_speech_keeps_the_question_open_until_its_final() -> None:
    session, adapter, orchestrator = _live(grace=0.05, continuation=1.0)
    await session.start("s")
    adapter.emit("Jenny, get me Hemas Holdings.")
    await _settle()
    adapter.emit_partial("total value")
    await _settle(0.15)  # past the grace period, but speech resumed
    assert orchestrator.asked == []
    adapter.emit("Total value as of last quarter.")
    await _settle()
    assert orchestrator.asked == ["get me Hemas Holdings. Total value as of last quarter."]
    await session.stop(_T2)


async def test_resumed_speech_that_never_finishes_falls_back_to_the_question_so_far() -> None:
    session, adapter, orchestrator = _live(grace=0.05, continuation=0.15)
    await session.start("s")
    adapter.emit("Jenny, get me Hemas Holdings.")
    await _settle()
    adapter.emit_partial("uh")
    await _settle(0.25)
    assert orchestrator.asked == ["get me Hemas Holdings."]
    await session.stop(_T2)


async def test_a_question_mark_is_answered_without_waiting() -> None:
    session, adapter, orchestrator = _live(grace=5.0)
    await session.start("s")
    adapter.emit("Jenny, what was revenue?")
    await _settle()
    assert orchestrator.asked == ["what was revenue?"]
    await session.stop(_T2)


async def test_pause_holds_the_question_and_resume_restarts_the_wait() -> None:
    session, adapter, orchestrator = _live(grace=0.05)
    await session.start("s")
    adapter.emit("Jenny, get me Hemas Holdings.")
    await _settle()
    await session.pause(_T1)
    await _settle(0.15)
    assert orchestrator.asked == []  # nothing is answered while paused
    await session.resume(_T2)
    await _settle(0.15)
    assert orchestrator.asked == ["get me Hemas Holdings."]
    await session.stop(_T2)


async def test_question_held_at_stop_is_answered() -> None:
    session, adapter, orchestrator = _live(grace=10.0)
    await session.start("s")
    adapter.emit("Jenny, get me Hemas Holdings.")
    await _settle()
    await session.stop(_T2)
    await session.wait_closed()
    assert orchestrator.asked == ["get me Hemas Holdings."]


# -------------------------------------------------------------- phrase list


def _sdk_with_phrase_list() -> tuple[FakeSDK, list[str]]:
    sdk = FakeSDK()
    FakeSDK.audio.PushAudioInputStream.owner = sdk
    added: list[str] = []
    grammar = SimpleNamespace(addPhrase=added.append)
    sdk.PhraseListGrammar = SimpleNamespace(from_recognizer=lambda recognizer: grammar)  # type: ignore[attr-defined]
    return sdk, added


async def test_azure_is_given_the_phrase_list() -> None:
    sdk, added = _sdk_with_phrase_list()
    adapter = AzureSpeechAdapter("key", "eastus", sdk=sdk, phrases=("Hemas", "Hemas Holdings", " ", "Jenny"))
    await adapter.start(lambda text: None, lambda: None)
    assert added == ["Hemas", "Hemas Holdings", "Jenny"]


async def test_no_phrase_list_without_phrases() -> None:
    sdk, added = _sdk_with_phrase_list()
    await AzureSpeechAdapter("key", "eastus", sdk=sdk).start(lambda text: None, lambda: None)
    assert added == []


_ENV = {
    "TLS_CERT_PATH": "cert.pem",
    "TLS_KEY_PATH": "key.pem",
    "BACKEND_AUTH_TOKEN": "tok",
    "AZURE_SPEECH_KEY": "k",
    "AZURE_SPEECH_REGION": "eastus",
    "TRIGGER_PHRASE": "Jenny",
}


def test_phrases_come_from_settings_plus_the_trigger_phrase() -> None:
    config = entry.load_config(dict(_ENV, AZURE_SPEECH_PHRASES=" Hemas , Hemas  Holdings,hemas,, JENNY "))
    assert config.speech_phrases == ("Hemas", "Hemas Holdings", "JENNY")
    assert entry.load_config(_ENV).speech_phrases == ("Jenny",)


@pytest.mark.parametrize(
    ("value", "message"),
    [("x" * 101, "100 characters"), (",".join(f"name{i}" for i in range(101)), "at most 100")],
)
def test_bad_phrase_lists_stop_startup(value: str, message: str) -> None:
    with pytest.raises(entry.ConfigError, match=message):
        entry.load_config(dict(_ENV, AZURE_SPEECH_PHRASES=value))
