"""Window events on the desktop: strict parsing, never breaking playback, honest display."""

from __future__ import annotations

import logging

import pytest

from playback.client import PlaybackClient
from session.controller import SessionState
from tests.test_controls import Clock, FakeStreamer
from tests.test_playback_client import Connector, RecordingPlayer, ScriptedSocket, _start
from ui.controls import Dialogue, SessionControls

# ------------------------------------------------------------------ client


async def test_events_reach_the_window_outside_and_inside_an_answer(caplog: pytest.LogCaptureFixture) -> None:
    socket = ScriptedSocket([
        {"ok": True},
        {"type": "trigger", "question_id": 1},
        {"type": "question_partial", "question_id": 1, "text": "what was"},
        {"type": "question", "question_id": 1, "text": "what was revenue?"},
        {"type": "answer_text", "question_id": 1, "text": "Revenue rose."},
        _start(1),
        b"\x01\x00",
        {"type": "trigger", "question_id": 2},
        b"\x02\x00",
        {"type": "answer_end", "answer_id": 1},
        {"type": "session_end"},
    ])
    events: list[dict[str, object]] = []
    player = RecordingPlayer()
    client = PlaybackClient(Connector(socket), player, on_event=events.append)
    with caplog.at_level(logging.DEBUG):
        await client.run("session-1")
    assert [event["type"] for event in events] == ["trigger", "question_partial", "question", "answer_text", "trigger"]
    assert player.answers == [b"\x01\x00\x02\x00"]
    assert client.ended
    assert "what was revenue" not in caplog.text
    assert "Revenue rose" not in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "question", "question_id": 1},
        {"type": "question", "question_id": 1, "text": 5},
        {"type": "trigger", "question_id": "1"},
        {"type": "trigger", "question_id": True},
        {"type": "trigger", "question_id": -1},
        {"type": "trigger", "question_id": 1, "text": "extra"},
        {"type": "answer_text", "question_id": 1, "text": "x" * 5001},
        {"type": "question", "question_id": 1, "text": "q", "speaker": "someone"},
    ],
)
async def test_malformed_events_are_protocol_errors(bad: dict[str, object]) -> None:
    socket = ScriptedSocket([{"ok": True}, bad, {"type": "session_end"}])
    events: list[dict[str, object]] = []
    client = PlaybackClient(Connector(socket), RecordingPlayer(), on_event=events.append)
    await client.run("session-1")
    assert events == []
    assert not client.ended


async def test_a_failing_window_handler_never_stops_playback() -> None:
    def broken(event: dict[str, object]) -> None:
        raise RuntimeError("window gone")

    socket = ScriptedSocket([
        {"ok": True},
        {"type": "trigger", "question_id": 1},
        _start(1), b"\x01\x00", {"type": "answer_end", "answer_id": 1},
        {"type": "session_end"},
    ])
    player = RecordingPlayer()
    client = PlaybackClient(Connector(socket), player, on_event=broken)
    await client.run("session-1")
    assert player.answers == [b"\x01\x00"] and client.ended


async def test_events_are_ignored_without_a_handler() -> None:
    socket = ScriptedSocket([{"ok": True}, {"type": "trigger", "question_id": 1}, {"type": "session_end"}])
    client = PlaybackClient(Connector(socket), RecordingPlayer())
    await client.run("session-1")
    assert client.ended


# ---------------------------------------------------------------- controls


async def _running() -> tuple[SessionControls, FakeStreamer]:
    streamer = FakeStreamer()
    controls = SessionControls(lambda: streamer.parts(), Clock())
    await controls.start()
    return controls, streamer


def _q(kind: str, question_id: int, text: str | None = None) -> dict[str, object]:
    event: dict[str, object] = {"type": kind, "question_id": question_id}
    if text is not None:
        event["text"] = text
    return event


async def test_question_then_answer_flow() -> None:
    controls, _ = await _running()
    controls.handle_event(_q("trigger", 1))
    view = controls.view()
    assert view.dialogue is Dialogue.HEARD and view.question == ""
    controls.handle_event(_q("question_partial", 1, "what was"))
    assert controls.view().question == "what was"
    controls.handle_event(_q("question", 1, "what was revenue?"))
    view = controls.view()
    assert view.dialogue is Dialogue.ASKED and view.question == "what was revenue?" and view.answer == ""
    controls.handle_event(_q("answer_text", 1, "Revenue rose."))
    view = controls.view()
    assert view.dialogue is Dialogue.ANSWERED and view.answer == "Revenue rose."


async def test_answer_stays_until_the_next_question_and_a_false_alarm_restores_it() -> None:
    controls, _ = await _running()
    for event in (_q("trigger", 1), _q("question", 1, "q1"), _q("answer_text", 1, "a1")):
        controls.handle_event(event)
    controls.handle_event(_q("trigger", 2))
    controls.handle_event(_q("question_partial", 2, "hmm"))
    assert controls.view().dialogue is Dialogue.HEARD
    controls.handle_event(_q("trigger_cancelled", 2))
    view = controls.view()
    assert (view.dialogue, view.question, view.answer) == (Dialogue.ANSWERED, "q1", "a1")

    controls.handle_event(_q("trigger", 3))
    controls.handle_event(_q("question", 3, "q3"))
    view = controls.view()
    assert (view.question, view.answer) == ("q3", "")


async def test_unavailable_and_no_question() -> None:
    controls, _ = await _running()
    controls.handle_event(_q("trigger", 1))
    controls.handle_event(_q("no_question", 1))
    assert controls.view().dialogue is Dialogue.NONE
    controls.handle_event(_q("trigger", 2))
    controls.handle_event(_q("question", 2, "share price?"))
    controls.handle_event(_q("answer_unavailable", 2))
    view = controls.view()
    assert view.dialogue is Dialogue.UNAVAILABLE and view.question == "share price?"


async def test_stale_and_malformed_events_are_ignored() -> None:
    controls, _ = await _running()
    controls.handle_event(_q("trigger", 2))
    controls.handle_event(_q("trigger", 1))
    controls.handle_event(_q("question", 1, "old question"))
    controls.handle_event({"type": "question", "question_id": "2", "text": "bad id"})
    controls.handle_event(_q("answer_text", 2, "answer before question"))
    view = controls.view()
    assert view.dialogue is Dialogue.HEARD and view.question == "" and view.answer == ""


async def test_text_is_cleared_on_stop_and_ignored_when_idle() -> None:
    controls, _ = await _running()
    controls.handle_event(_q("trigger", 1))
    controls.handle_event(_q("question", 1, "q"))
    await controls.stop()
    view = controls.view()
    assert (view.dialogue, view.question, view.answer) == (Dialogue.NONE, "", "")
    controls.handle_event(_q("trigger", 5))
    assert controls.view().dialogue is Dialogue.NONE


async def test_new_session_starts_with_an_empty_card() -> None:
    controls, streamer = await _running()
    controls.handle_event(_q("trigger", 1))
    controls.handle_event(_q("question", 1, "q"))
    await controls.stop()
    streamer.state = SessionState.IDLE
    await controls.start()
    view = controls.view()
    assert view.question == "" and view.dialogue is Dialogue.NONE
    controls.handle_event(_q("trigger", 1))
    assert controls.view().dialogue is Dialogue.HEARD
