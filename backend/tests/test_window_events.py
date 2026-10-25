"""Window events: an immediate trigger cue, the question live, then the answer.

Security properties: nothing said before the trigger phrase is sent, a cue
from in-progress text never starts analysis, and paused speech sends nothing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orchestration.live import LiveSession
from orchestration.playback import PlaybackHub
from orchestration.question import text_after_phrase
from stt.phrase_detector import PhraseDetector
from stt.speech import AzureSpeechAdapter, SpeechToText
from stt.transcript import SessionTranscript
from tests.test_speech import FakeSDK, MemoryAdapter

_T1 = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
_T2 = datetime(2026, 10, 26, 9, 9, tzinfo=timezone.utc)


class Events:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def notify(self, session_id: str, event: dict[str, object]) -> None:
        assert session_id == "session-1"
        self.sent.append(event)

    def types(self) -> list[object]:
        return [event["type"] for event in self.sent]

    def texts(self) -> str:
        return " | ".join(str(event.get("text", "")) for event in self.sent)


class Orchestrator:
    def __init__(self, answer: str = "Revenue rose 8 percent.") -> None:
        self.asked: list[str] = []
        self._answer = answer

    async def answer(self, transcript_slice: str, question: str) -> str:
        self.asked.append(question)
        return self._answer


def _live(orchestrator: Orchestrator | None = None, events: Events | None = None):
    adapter = MemoryAdapter()
    events = events if events is not None else Events()
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("Jenny"),
        orchestrator=orchestrator if orchestrator is not None else Orchestrator(),
        events=events,
    )
    return session, adapter, events


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_trigger_in_progress_text_cues_the_window_at_once() -> None:
    orchestrator = Orchestrator()
    session, adapter, events = _live(orchestrator)
    await session.start("session-1")
    adapter.emit_partial("secret before the phrase Jenny")
    await _settle()
    assert events.types() == ["trigger"]
    adapter.emit_partial("secret before the phrase Jenny what was")
    await _settle()
    assert events.sent[-1] == {"type": "question_partial", "question_id": 1, "text": "what was"}
    # A cue from in-progress text never starts analysis.
    assert orchestrator.asked == []
    assert "secret" not in events.texts()
    await session.stop(_T2)


async def test_full_question_and_answer_events_in_order(caplog: pytest.LogCaptureFixture) -> None:
    orchestrator = Orchestrator()
    session, adapter, events = _live(orchestrator)
    with caplog.at_level(logging.DEBUG):
        await session.start("session-1")
        adapter.emit("good morning everyone")
        adapter.emit_partial("Jenny what was")
        adapter.emit_partial("Jenny what was our Q3 revenue")
        adapter.emit("Jenny, what was our Q3 revenue?")
        await _settle()
        await session.stop(_T2)
        await session.wait_closed()
    assert events.types() == ["trigger", "question_partial", "question_partial", "question", "answer_text"]
    assert {event["question_id"] for event in events.sent} == {1}
    assert events.sent[3]["text"] == "what was our Q3 revenue?"
    assert events.sent[4]["text"] == "Revenue rose 8 percent."
    assert orchestrator.asked == ["what was our Q3 revenue?"]
    assert "good morning" not in events.texts()
    assert "Q3 revenue" not in caplog.text


async def test_phrase_ending_an_utterance_shows_the_next_one_live() -> None:
    session, adapter, events = _live()
    await session.start("session-1")
    adapter.emit("Jenny.")
    await _settle()
    assert events.types() == ["trigger"]
    adapter.emit_partial("how are sales")
    await _settle()
    assert events.sent[-1] == {"type": "question_partial", "question_id": 1, "text": "how are sales"}
    adapter.emit("How are sales in Colombo?")
    await _settle()
    assert events.types()[-2:] == ["question", "answer_text"]
    await session.stop(_T2)


async def test_cue_the_final_text_does_not_confirm_is_cancelled() -> None:
    orchestrator = Orchestrator()
    session, adapter, events = _live(orchestrator)
    await session.start("session-1")
    adapter.emit_partial("Jenny")
    adapter.emit("Jen needs the slides")
    await _settle()
    assert events.types() == ["trigger", "trigger_cancelled"]
    assert orchestrator.asked == []
    await session.stop(_T2)


async def test_partials_without_the_phrase_send_nothing() -> None:
    session, adapter, events = _live()
    await session.start("session-1")
    adapter.emit_partial("the board discussed the merger")
    adapter.emit("the board discussed the merger")
    await _settle()
    assert events.sent == []
    await session.stop(_T2)


async def test_phrase_without_a_question_and_failed_answer_are_reported() -> None:
    session, adapter, events = _live(Orchestrator(answer=""))
    await session.start("session-1")
    adapter.emit("Jenny.")
    adapter.emit("um")
    await _settle()
    assert events.types() == ["trigger", "no_question"]
    adapter.emit("Jenny, what is the share price?")
    await _settle()
    assert events.types()[-3:] == ["trigger", "question", "answer_unavailable"]
    assert events.sent[-1]["question_id"] == 2
    await session.stop(_T2)


async def test_nothing_is_sent_while_paused() -> None:
    session, adapter, events = _live()
    await session.start("session-1")
    adapter.emit_partial("Jenny what")
    await _settle()
    await session.pause(_T1)
    adapter.emit_partial("Jenny secret side conversation")
    await _settle()
    assert events.types() == ["trigger", "question_partial", "trigger_cancelled"]
    assert "secret" not in events.texts()
    await session.resume(_T2)
    await session.stop(_T2)


async def test_answer_dropped_during_pause_is_reported_unavailable() -> None:
    release = asyncio.Event()

    class Slow(Orchestrator):
        async def answer(self, transcript_slice: str, question: str) -> str:
            await release.wait()
            return "late"

    session, adapter, events = _live(Slow())
    await session.start("session-1")
    adapter.emit("Jenny, what was revenue?")
    await _settle()
    await session.pause(_T1)
    release.set()
    await _settle()
    assert events.types()[-1] == "answer_unavailable"
    assert "late" not in events.texts()
    await session.stop(_T2)


async def test_event_failures_never_affect_the_session() -> None:
    class Broken(Events):
        def notify(self, session_id: str, event: dict[str, object]) -> None:
            raise RuntimeError("window gone")

    orchestrator = Orchestrator()
    session, adapter, _events = _live(orchestrator, Broken())
    await session.start("session-1")
    adapter.emit_partial("Jenny what")
    adapter.emit("Jenny, what was revenue?")
    await _settle()
    assert orchestrator.asked == ["what was revenue?"]
    assert "Assistant: Revenue rose 8 percent." in session.transcript.render()
    await session.stop(_T2)


async def test_without_events_partials_are_not_requested() -> None:
    adapter = MemoryAdapter()
    session = LiveSession(SpeechToText(adapter), SessionTranscript(), PhraseDetector("Jenny"))
    await session.start("session-1")
    adapter.emit_partial("Jenny what")  # ignored: no partial listener
    await session.stop(_T2)


def test_text_after_phrase_keeps_only_what_follows() -> None:
    assert text_after_phrase("private remark. Jenny, what was revenue?", "Jenny") == "what was revenue?"
    assert text_after_phrase("Jenny", "Jenny") == ""
    assert text_after_phrase("no phrase here", "Jenny") is None
    assert text_after_phrase("Jennyfer said", "Jenny") is None
    assert text_after_phrase("anything", "") is None


# ------------------------------------------------------------------ speech


async def test_speech_passes_partials_only_while_running() -> None:
    adapter = MemoryAdapter()
    speech = SpeechToText(adapter)
    partials: list[str] = []
    speech.set_partial_listener(partials.append)
    await speech.start_session()
    adapter.emit_partial("hello")
    adapter.emit_partial("   ")
    await speech.pause()
    adapter.emit_partial("paused words")
    await speech.resume()
    adapter.emit_partial("again")
    await speech.stop()
    assert partials == ["hello", "again"]


async def test_azure_adapter_forwards_recognizing_text_only() -> None:
    sdk = FakeSDK()
    FakeSDK.audio.PushAudioInputStream.owner = sdk
    adapter = AzureSpeechAdapter("key", "eastus", sdk=sdk)
    partials: list[str] = []
    await adapter.start(lambda text: None, lambda: None, on_partial=partials.append)
    handler = sdk.recognizer.recognizing.callbacks[0]
    handler(SimpleNamespace(result=SimpleNamespace(reason="recognizing", text="Jenny what")))
    handler(SimpleNamespace(result=SimpleNamespace(reason="nomatch", text="noise")))
    handler(SimpleNamespace(result=None))
    assert partials == ["Jenny what"]


async def test_azure_adapter_skips_partials_when_not_requested() -> None:
    sdk = FakeSDK()
    FakeSDK.audio.PushAudioInputStream.owner = sdk
    adapter = AzureSpeechAdapter("key", "eastus", sdk=sdk)
    await adapter.start(lambda text: None, lambda: None)
    assert sdk.recognizer.recognizing.callbacks == []


# --------------------------------------------------------------------- hub


class Socket:
    def __init__(self, fail: bool = False) -> None:
        self.frames: list[str | bytes] = []
        self._fail = fail

    async def send(self, data: str | bytes) -> None:
        if self._fail:
            raise ConnectionError("gone")
        self.frames.append(data)

    async def close(self) -> None:
        return None


class SilentTTS:
    async def synthesize(self, text: str):
        yield b"\x00\x00"


async def test_hub_sends_events_at_once_and_drops_them_without_a_window() -> None:
    hub = PlaybackHub(SilentTTS())
    hub.open("s")
    hub.notify("s", {"type": "trigger", "question_id": 1})  # no window yet: dropped
    socket = Socket()
    assert hub.attach("s", socket)
    hub.notify("s", {"type": "trigger", "question_id": 2})
    hub.notify("missing", {"type": "trigger", "question_id": 3})
    await _settle()
    assert socket.frames == ['{"type":"trigger","question_id":2}']
    hub.close("s")
    await hub.wait_closed("s")


async def test_hub_event_send_failure_is_contained() -> None:
    hub = PlaybackHub(SilentTTS())
    hub.open("s")
    assert hub.attach("s", Socket(fail=True))
    hub.notify("s", {"type": "trigger", "question_id": 1})
    await _settle()
    hub.close("s")
    await hub.wait_closed("s")
