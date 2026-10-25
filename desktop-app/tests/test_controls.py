"""Window session controls: local commands only, honest status, clean teardown."""

from __future__ import annotations

import asyncio

import pytest

from session.controller import SessionState
from ui.controls import SessionControls, SessionParts, Status, describe_error


class TransportError(Exception):
    """Stands in for transport.client.TransportError (matched by class name)."""


class FakeStreamer:
    def __init__(self, fail_on: str | None = None, error: type[Exception] = RuntimeError) -> None:
        self.calls: list[str] = []
        self.state = SessionState.IDLE
        self.forwarding = False
        self.speaking = False
        self.closed = False
        self.playback_done = asyncio.Event()
        self.playback_done.set()
        self._fail_on = fail_on
        self._error = error

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name == self._fail_on:
            raise self._error("secret meeting words")

    async def start(self) -> None:
        self._call("start")
        self.state = SessionState.RUNNING
        self.forwarding = True

    async def pause(self) -> None:
        self._call("pause")
        self.state = SessionState.PAUSED

    async def resume(self) -> None:
        self._call("resume")
        self.state = SessionState.RUNNING

    async def stop(self) -> None:
        self._call("stop")
        self.state = SessionState.IDLE
        self.forwarding = False

    async def wait_playback(self) -> None:
        self.calls.append("wait_playback")
        await self.playback_done.wait()

    def parts(self) -> SessionParts:
        def close() -> None:
            self.closed = True

        return SessionParts(self, lambda: self.state, lambda: self.speaking, close)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _controls(streamer: FakeStreamer, clock: Clock | None = None) -> tuple[SessionControls, list[int]]:
    builds: list[int] = []

    def build() -> SessionParts:
        builds.append(1)
        return streamer.parts()

    return SessionControls(build, clock or Clock()), builds


async def test_idle_view_only_allows_start() -> None:
    controls, builds = _controls(FakeStreamer())
    view = controls.view()
    assert view.status is Status.IDLE
    assert (view.can_start, view.can_pause, view.can_resume, view.can_stop) == (True, False, False, False)
    assert builds == []
    assert controls.active is False


async def test_full_cycle_and_listening_time_excludes_pause() -> None:
    clock = Clock()
    streamer = FakeStreamer()
    controls, _ = _controls(streamer, clock)

    await controls.start()
    assert controls.view().status is Status.LISTENING
    assert controls.view().can_pause and controls.view().can_stop
    clock.now += 30
    await controls.pause()
    clock.now += 600
    view = controls.view()
    assert view.status is Status.PAUSED
    assert view.can_resume and not view.can_pause
    assert view.listened_seconds == pytest.approx(30)

    await controls.resume()
    clock.now += 10
    assert controls.view().listened_seconds == pytest.approx(40)
    await controls.stop()
    assert streamer.calls == ["start", "pause", "resume", "stop", "wait_playback"]
    assert streamer.closed
    assert controls.view().status is Status.IDLE
    assert controls.view().listened_seconds == pytest.approx(40)
    assert controls.active is False


async def test_each_start_builds_a_fresh_session() -> None:
    controls, builds = _controls(FakeStreamer())
    await controls.start()
    await controls.stop()
    await controls.start()
    assert len(builds) == 2
    assert controls.view().listened_seconds == 0


async def test_commands_out_of_order_do_nothing() -> None:
    streamer = FakeStreamer()
    controls, _ = _controls(streamer)
    await controls.pause()
    await controls.resume()
    await controls.stop()
    await controls.start()
    await controls.start()
    await controls.resume()
    assert streamer.calls == ["start"]


async def test_start_failure_releases_capture_and_shows_class_only() -> None:
    streamer = FakeStreamer(fail_on="start", error=TransportError)
    controls, _ = _controls(streamer)
    await controls.start()
    view = controls.view()
    assert view.status is Status.IDLE and view.can_start
    assert "backend did not respond" in view.error
    assert "secret" not in view.error
    assert streamer.calls == ["start", "stop", "wait_playback"]
    assert streamer.closed
    controls.clear_error()
    assert controls.view().error == ""


async def test_build_failure_is_reported_and_leaves_idle() -> None:
    def build() -> SessionParts:
        raise OSError("no device")

    controls = SessionControls(build, Clock())
    await controls.start()
    assert controls.view().status is Status.IDLE
    assert controls.view().error == "Could not start (OSError)."


async def test_capture_that_paused_itself_is_interrupted_not_resumable() -> None:
    # A device failure pauses capture on its own. The window must not offer
    # Resume as if the room had chosen to pause; only Stop is allowed.
    streamer = FakeStreamer()
    controls, _ = _controls(streamer)
    await controls.start()
    streamer.state = SessionState.PAUSED
    view = controls.view()
    assert view.status is Status.INTERRUPTED
    assert (view.can_pause, view.can_resume, view.can_stop) == (False, False, True)
    await controls.resume()
    assert "resume" not in streamer.calls


async def test_lost_link_is_interrupted() -> None:
    streamer = FakeStreamer()
    controls, _ = _controls(streamer)
    await controls.start()
    streamer.forwarding = False
    assert controls.view().status is Status.INTERRUPTED
    await controls.stop()
    assert controls.view().status is Status.IDLE


async def test_speaking_is_reported_while_listening() -> None:
    streamer = FakeStreamer()
    controls, _ = _controls(streamer)
    await controls.start()
    streamer.speaking = True
    view = controls.view()
    assert view.speaking and view.status is Status.LISTENING


async def test_stop_waits_for_playing_answer_and_shows_finishing() -> None:
    streamer = FakeStreamer()
    controls, _ = _controls(streamer)
    await controls.start()
    streamer.speaking = True
    streamer.playback_done.clear()
    stopping = asyncio.create_task(controls.stop())
    for _ in range(10):
        await asyncio.sleep(0)
    view = controls.view()
    assert view.status is Status.FINISHING
    assert view.speaking
    assert not (view.can_start or view.can_stop)
    assert controls.active
    streamer.playback_done.set()
    await stopping
    assert controls.view().status is Status.IDLE
    assert streamer.closed


async def test_stop_failure_still_tears_down() -> None:
    streamer = FakeStreamer(fail_on="stop")
    controls, _ = _controls(streamer)
    await controls.start()
    await controls.stop()
    assert streamer.closed
    view = controls.view()
    assert view.status is Status.IDLE
    assert view.error == "Could not stop cleanly (RuntimeError)."


async def test_pause_and_resume_failures_are_reported() -> None:
    streamer = FakeStreamer(fail_on="pause")
    controls, _ = _controls(streamer)
    await controls.start()
    await controls.pause()
    assert controls.view().error == "Could not pause (RuntimeError)."
    assert controls.view().status is Status.LISTENING

    streamer = FakeStreamer(fail_on="resume")
    controls, _ = _controls(streamer)
    await controls.start()
    await controls.pause()
    await controls.resume()
    view = controls.view()
    assert view.error == "Could not resume (RuntimeError)."
    assert view.status is Status.PAUSED


async def test_cleanup_errors_do_not_escape() -> None:
    streamer = FakeStreamer()

    async def broken_wait() -> None:
        raise RuntimeError("x")

    streamer.wait_playback = broken_wait  # type: ignore[method-assign]
    parts = streamer.parts()

    def broken_close() -> None:
        raise RuntimeError("y")

    controls = SessionControls(lambda: SessionParts(streamer, parts.state, parts.speaking, broken_close), Clock())
    await controls.start()
    await controls.stop()
    assert controls.view().status is Status.IDLE


def test_describe_error_never_includes_the_message() -> None:
    assert describe_error("start", ValueError("board secret")) == "Could not start (ValueError)."


def test_controls_module_has_no_network_imports() -> None:
    import ui.controls as module

    source = open(module.__file__, encoding="utf-8").read()
    for name in ("websockets", "socket", "http", "urllib", "transport"):
        assert f"import {name}" not in source
        assert f"from {name}" not in source
