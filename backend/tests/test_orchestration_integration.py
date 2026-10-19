"""Integration test D: phrase -> slice -> question -> orchestrator -> stub tools -> transcript.

The SDK is faked, but it calls the real stub tool handlers and the real
LiveSession, transcript, detector, and question extraction. Meeting text
keeps landing while the answer is pending, and STT is never stopped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from orchestration.live import LiveSession
from orchestration.orchestrator import Orchestrator
from stt.phrase_detector import PhraseDetector
from stt.speech import SpeechToText
from stt.transcript import SessionTranscript
from tests.conftest import result_message
from tests.test_speech import MemoryAdapter
from tools.external_tool.server import search_external
from tools.genie_tool.server import query_internal_data

_T1 = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
_T2 = datetime(2026, 1, 1, 14, 11, tzinfo=timezone.utc)


def _stub_backed_query(fake_query, release: asyncio.Event | None = None):
    asked: list[str] = []

    async def respond(prompt, options):
        question = prompt.split("<question>")[1].split("</question>")[0]
        asked.append(question)
        internal = await query_internal_data.handler({"question": question})
        external = await search_external.handler({"question": question})
        if release is not None:
            await release.wait()
        text = " ".join(
            block["text"] for result in (internal, external) for block in result["content"]
        )
        return [result_message(text)]

    return fake_query(respond), asked


class FakeDelivery:
    def __init__(self, fail: bool = False) -> None:
        self.opened: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self._fail = fail

    def open(self, session_id: str) -> None:
        self.opened.append(session_id)

    def publish(self, session_id: str, text: str) -> bool:
        if self._fail:
            raise RuntimeError("speaker unplugged")
        self.published.append((session_id, text))
        return True

    def close(self, session_id: str) -> None:
        self.closed.append(session_id)


def _live(orchestrator, delivery=None):
    adapter = MemoryAdapter()
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("board assistant"),
        orchestrator=orchestrator,
        delivery=delivery,
    )
    return session, adapter


async def _stop(session) -> None:
    await session.stop(_T2)
    await session.wait_closed()


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_gate_stub_answer_lands_in_transcript_while_stt_continues(fake_query, caplog) -> None:
    release = asyncio.Event()
    query, asked = _stub_backed_query(fake_query, release)
    delivery = FakeDelivery()
    session, adapter = _live(Orchestrator(query=query), delivery)
    with caplog.at_level(logging.DEBUG):
        await session.start("session-1")
        adapter.emit("good morning everyone")
        adapter.emit("board assistant what was Q3 revenue")
        await _settle()
        adapter.emit("while we wait, next item")
        assert "Assistant:" not in session.transcript.render()
        release.set()
        await _settle()
        adapter.emit("after the answer")
        await _stop(session)

    assert asked == ["what was Q3 revenue"]
    lines = session.transcript.render().splitlines()
    assistant = [line for line in lines if line.startswith("Assistant: ")]
    assert len(assistant) == 1
    assert "[source: internal data (stub)] Q3 revenue was 12.4 billion rupees" in assistant[0]
    assert "[source: external news (stub)]" in assistant[0]
    assert lines.index("while we wait, next item") < lines.index(assistant[0])
    assert lines.index(assistant[0]) < lines.index("after the answer")
    assert delivery.opened == ["session-1"]
    assert delivery.published == [("session-1", assistant[0].removeprefix("Assistant: "))]
    assert delivery.closed == ["session-1"]
    assert adapter.stop_calls == 1
    assert "Q3 revenue" not in caplog.text
    assert "12.4" not in caplog.text


async def test_phrase_without_question_does_not_call_the_agent(fake_query) -> None:
    query, asked = _stub_backed_query(fake_query)
    session, adapter = _live(Orchestrator(query=query))
    await session.start("session-1")
    adapter.emit("board assistant")
    adapter.emit("um")
    await _stop(session)
    assert asked == []
    assert "Assistant:" not in session.transcript.render()


async def test_answer_arriving_during_pause_is_not_appended_or_delivered(fake_query) -> None:
    release = asyncio.Event()
    query, _asked = _stub_backed_query(fake_query, release)
    delivery = FakeDelivery()
    session, adapter = _live(Orchestrator(query=query), delivery)
    await session.start("session-1")
    adapter.emit("board assistant what was Q3 revenue")
    await _settle()
    await session.pause(_T1)
    release.set()
    await _settle()
    await session.resume(_T2)
    await _stop(session)
    assert "Assistant:" not in session.transcript.render()
    assert delivery.published == []


async def test_delivery_failure_keeps_the_assistant_turn(fake_query) -> None:
    query, _asked = _stub_backed_query(fake_query)

    session, adapter = _live(Orchestrator(query=query), FakeDelivery(fail=True))
    await session.start("session-1")
    adapter.emit("board assistant what was Q3 revenue")
    await _stop(session)
    assert "Assistant: [source: internal data (stub)]" in session.transcript.render()


async def test_stop_cancels_an_answer_that_never_finishes(fake_query) -> None:
    query, _asked = _stub_backed_query(fake_query, asyncio.Event())
    adapter = MemoryAdapter()
    session = LiveSession(
        SpeechToText(adapter),
        SessionTranscript(),
        PhraseDetector("board assistant"),
        orchestrator=Orchestrator(query=query),
        answer_timeout=0.01,
    )
    await session.start("session-1")
    adapter.emit("board assistant what was Q3 revenue")
    await _stop(session)
    assert "Assistant:" not in session.transcript.render()


@pytest.mark.integration
async def test_live_agent_sdk_answers_from_the_stubs() -> None:
    """Real Agent SDK call against the stub tools. Needs ANTHROPIC_API_KEY."""

    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not set")
    session, adapter = _live(Orchestrator())
    await session.start("session-1")
    adapter.emit("good morning everyone")
    adapter.emit("board assistant what was Q3 revenue")
    adapter.emit("next item on the agenda")
    await _stop(session)
    rendered = session.transcript.render()
    assert "Assistant: " in rendered
    assert "12.4" in rendered
