"""Answer delivery: ordered, non-blocking, drained on Stop, TTS failures reported."""

from __future__ import annotations

import asyncio
import json

import pytest

from orchestration.playback import PlaybackHub
from tts.speech import SynthesisFailed


class FakeTTS:
    def __init__(self, fail_on: str | None = None, gate: asyncio.Event | None = None) -> None:
        self.spoken: list[str] = []
        self._fail_on = fail_on
        self._gate = gate

    async def synthesize(self, text: str):
        self.spoken.append(text)
        yield f"{text}:1".encode()
        if self._gate is not None:
            await self._gate.wait()
        if text == self._fail_on:
            raise SynthesisFailed("canceled")
        yield f"{text}:2".encode()


class FakeSubscriber:
    def __init__(self, fail_after: int | None = None) -> None:
        self.frames: list[str | bytes] = []
        self.closed = False
        self._fail_after = fail_after

    async def send(self, data: str | bytes) -> None:
        if self._fail_after is not None and len(self.frames) >= self._fail_after:
            raise ConnectionError("gone")
        self.frames.append(data)

    async def close(self) -> None:
        self.closed = True

    def kinds(self) -> list[str]:
        out = []
        for frame in self.frames:
            out.append(json.loads(frame)["type"] if isinstance(frame, str) else frame.decode())
        return out


async def _drain(hub: PlaybackHub, session_id: str) -> None:
    hub.close(session_id)
    await asyncio.wait_for(hub.wait_closed(session_id), timeout=2)


async def test_answers_are_spoken_in_order_and_session_end_follows() -> None:
    hub = PlaybackHub(FakeTTS())
    hub.open("s1")
    sub = FakeSubscriber()
    assert hub.attach("s1", sub)
    assert hub.publish("s1", "a")
    assert hub.publish("s1", "b")
    await _drain(hub, "s1")
    assert sub.kinds() == [
        "answer_start", "a:1", "a:2", "answer_end",
        "answer_start", "b:1", "b:2", "answer_end",
        "session_end",
    ]
    starts = [json.loads(f) for f in sub.frames if isinstance(f, str) and "answer_start" in f]
    assert [s["answer_id"] for s in starts] == [1, 2]
    assert starts[0]["sample_rate"] == 16000
    assert sub.closed
    assert not hub.is_open("s1")


async def test_publish_never_waits_for_speech() -> None:
    gate = asyncio.Event()
    hub = PlaybackHub(FakeTTS(gate=gate))
    hub.open("s1")
    hub.attach("s1", FakeSubscriber())
    for text in ("a", "b", "c"):
        assert hub.publish("s1", text) is True
    gate.set()
    await _drain(hub, "s1")


async def test_answers_wait_for_a_late_subscriber() -> None:
    hub = PlaybackHub(FakeTTS())
    hub.open("s1")
    hub.publish("s1", "early")
    await asyncio.sleep(0.01)
    sub = FakeSubscriber()
    hub.attach("s1", sub)
    await _drain(hub, "s1")
    assert "early:2" in sub.kinds()


async def test_tts_failure_sends_answer_error_and_continues() -> None:
    hub = PlaybackHub(FakeTTS(fail_on="bad"))
    hub.open("s1")
    sub = FakeSubscriber()
    hub.attach("s1", sub)
    hub.publish("s1", "bad")
    hub.publish("s1", "good")
    await _drain(hub, "s1")
    assert sub.kinds() == [
        "answer_start", "bad:1", "answer_error",
        "answer_start", "good:1", "good:2", "answer_end",
        "session_end",
    ]


async def test_stop_while_speaking_still_finishes_the_answer() -> None:
    gate = asyncio.Event()
    hub = PlaybackHub(FakeTTS(gate=gate))
    hub.open("s1")
    sub = FakeSubscriber()
    hub.attach("s1", sub)
    hub.publish("s1", "long")
    await asyncio.sleep(0.01)
    hub.close("s1")
    assert not hub.publish("s1", "after stop")
    gate.set()
    await asyncio.wait_for(hub.wait_closed("s1"), timeout=2)
    assert sub.kinds() == ["answer_start", "long:1", "long:2", "answer_end", "session_end"]


async def test_drain_timeout_closes_a_stuck_channel() -> None:
    hub = PlaybackHub(FakeTTS(gate=asyncio.Event()), drain_timeout=0.05)
    hub.open("s1")
    sub = FakeSubscriber()
    hub.attach("s1", sub)
    hub.publish("s1", "stuck")
    await asyncio.sleep(0.01)
    await _drain(hub, "s1")
    assert sub.closed
    assert "session_end" not in sub.kinds()


async def test_subscriber_lost_mid_answer_is_detached_and_can_resubscribe() -> None:
    hub = PlaybackHub(FakeTTS())
    hub.open("s1")
    first = FakeSubscriber(fail_after=2)
    hub.attach("s1", first)
    hub.publish("s1", "a")
    await asyncio.sleep(0.01)
    second = FakeSubscriber()
    assert hub.attach("s1", second)
    hub.publish("s1", "b")
    await _drain(hub, "s1")
    assert second.kinds() == ["answer_start", "b:1", "b:2", "answer_end", "session_end"]


async def test_attach_refuses_unknown_sessions_and_a_second_socket() -> None:
    hub = PlaybackHub(FakeTTS())
    assert not hub.attach("missing", FakeSubscriber())
    hub.open("s1")
    hub.open("s1")
    assert hub.attach("s1", FakeSubscriber())
    assert not hub.attach("s1", FakeSubscriber())
    await hub.shutdown()


async def test_bounded_queue_drops_extra_answers_and_blank_text() -> None:
    hub = PlaybackHub(FakeTTS(), max_pending=1)
    hub.open("s1")
    assert hub.publish("s1", "one")
    assert not hub.publish("s1", "two")
    assert not hub.publish("s1", "   ")
    assert not hub.publish("other", "x")
    hub.attach("s1", FakeSubscriber())
    await _drain(hub, "s1")


async def test_detach_ignores_strangers_and_close_is_idempotent() -> None:
    hub = PlaybackHub(FakeTTS())
    hub.open("s1")
    sub = FakeSubscriber()
    hub.attach("s1", sub)
    hub.detach("s1", FakeSubscriber())
    hub.detach("missing", sub)
    hub.close("s1")
    hub.close("s1")
    hub.close("missing")
    await asyncio.wait_for(hub.wait_closed("s1"), timeout=2)
    await hub.wait_closed("missing")


def test_max_pending_must_be_positive() -> None:
    with pytest.raises(ValueError):
        PlaybackHub(FakeTTS(), max_pending=0)
