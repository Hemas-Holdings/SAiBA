"""Session controls behind the desktop window.

Turns local button presses into Start / Pause / Resume / Stop on a session
streamer and reports what the window should show. Presses come only from
this machine's window; nothing here listens on the network. No audio passes
through this module. It holds the local session state, whether an answer is
playing, listening time, a short error message built from the failing
exception's class (never its text), and, from the backend's window events,
the latest question and answer only. That text is kept in memory for display,
never logged or saved, replaced by the next question, and cleared on Stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from session.controller import SessionState

logger = logging.getLogger(__name__)


class Streamer(Protocol):
    @property
    def forwarding(self) -> bool: ...

    async def start(self) -> object: ...

    async def pause(self) -> object: ...

    async def resume(self) -> object: ...

    async def stop(self) -> object: ...

    async def wait_playback(self) -> None: ...


@dataclass(frozen=True)
class SessionParts:
    """One session's wiring. A fresh set is built for every Start."""

    streamer: Streamer
    state: Callable[[], SessionState]
    speaking: Callable[[], bool]
    close: Callable[[], None]


class Dialogue(Enum):
    NONE = "none"
    HEARD = "heard"
    ASKED = "asked"
    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"


class Status(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    PAUSED = "paused"
    STOPPING = "stopping"
    FINISHING = "finishing"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class View:
    status: Status
    speaking: bool
    listened_seconds: float
    error: str
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_stop: bool
    dialogue: Dialogue = Dialogue.NONE
    question: str = ""
    answer: str = ""


def describe_error(action: str, error: BaseException) -> str:
    """A short message for the window. Uses the class name only, never the text."""

    name = type(error).__name__
    if name == "TransportError":
        return f"Could not {action}: the assistant backend did not respond."
    return f"Could not {action} ({name})."


class SessionControls:
    """Local Start / Pause / Resume / Stop for the window, one command at a time."""

    def __init__(
        self,
        build: Callable[[], SessionParts],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._build = build
        self._clock = clock
        self._parts: SessionParts | None = None
        self._busy: Status | None = None
        self._finishing = False
        self._paused_by_user = False
        self._listened = 0.0
        self._running_since: float | None = None
        self._error = ""
        self._lock = asyncio.Lock()
        self._clear_dialogue()

    @property
    def active(self) -> bool:
        """True from Start until Stop has finished any answer that was playing."""

        return self._parts is not None or self._busy is not None

    def clear_error(self) -> None:
        self._error = ""

    def handle_event(self, event: dict[str, object]) -> None:
        """Apply one window event from the backend. Text is shown, never logged."""

        if self._parts is None:
            return
        kind = event.get("type")
        question_id = event.get("question_id")
        text = event.get("text")
        text = text if isinstance(text, str) else ""
        if not isinstance(question_id, int):
            return
        if kind == "trigger":
            if question_id <= self._question_id:
                return
            self._question_id = question_id
            # Keep what is on screen until this question is confirmed.
            self._previous = (self._dialogue, self._question, self._answer)
            self._dialogue = Dialogue.HEARD
            self._live_question = ""
            return
        if question_id != self._question_id:
            return
        if kind == "question_partial" and self._dialogue is Dialogue.HEARD:
            self._live_question = text
        elif kind == "question":
            self._dialogue = Dialogue.ASKED
            self._question = text
            self._answer = ""
            self._live_question = ""
        elif kind == "answer_text" and self._dialogue is Dialogue.ASKED:
            self._dialogue = Dialogue.ANSWERED
            self._answer = text
        elif kind == "answer_unavailable" and self._dialogue is Dialogue.ASKED:
            self._dialogue = Dialogue.UNAVAILABLE
        elif kind in ("trigger_cancelled", "no_question") and self._dialogue is Dialogue.HEARD:
            self._dialogue, self._question, self._answer = self._previous
            self._live_question = ""

    async def start(self) -> None:
        if self._lock.locked() or self._parts is not None:
            return
        async with self._lock:
            self._error = ""
            self._busy = Status.CONNECTING
            parts: SessionParts | None = None
            try:
                parts = self._build()
                await parts.streamer.start()
            except Exception as error:  # noqa: BLE001 - shown to the user, then cleaned up
                logger.warning("window start failed error=%s", type(error).__name__)
                self._error = describe_error("start", error)
                if parts is not None:
                    try:
                        # Release capture if it opened before the link failed.
                        await parts.streamer.stop()
                    except Exception:  # noqa: BLE001 - capture never opened
                        pass
                    await self._teardown(parts)
                return
            finally:
                self._busy = None
            self._parts = parts
            self._clear_dialogue()
            self._paused_by_user = False
            self._listened = 0.0
            self._running_since = self._clock()
            logger.info("window session started")

    async def pause(self) -> None:
        parts = self._parts
        if self._lock.locked() or parts is None or self._status(parts) is not Status.LISTENING:
            return
        async with self._lock:
            try:
                await parts.streamer.pause()
            except Exception as error:  # noqa: BLE001 - shown to the user
                logger.warning("window pause failed error=%s", type(error).__name__)
                self._error = describe_error("pause", error)
            self._settle_clock()
            if parts.state() is SessionState.PAUSED:
                self._paused_by_user = True

    async def resume(self) -> None:
        parts = self._parts
        if self._lock.locked() or parts is None or self._status(parts) is not Status.PAUSED:
            return
        async with self._lock:
            try:
                await parts.streamer.resume()
            except Exception as error:  # noqa: BLE001 - shown to the user
                logger.warning("window resume failed error=%s", type(error).__name__)
                self._error = describe_error("resume", error)
                return
            self._paused_by_user = False
            self._running_since = self._clock()

    async def stop(self) -> None:
        """End capture now; an answer that is already playing is allowed to finish."""

        parts = self._parts
        if self._lock.locked() or parts is None:
            return
        async with self._lock:
            self._busy = Status.STOPPING
            self._settle_clock()
            try:
                await parts.streamer.stop()
            except Exception as error:  # noqa: BLE001 - still tear down below
                logger.warning("window stop failed error=%s", type(error).__name__)
                self._error = describe_error("stop cleanly", error)
            self._busy = None
            self._finishing = True
            try:
                await self._teardown(parts)
            finally:
                self._finishing = False
                self._parts = None
                self._paused_by_user = False
                self._clear_dialogue()
            logger.info("window session stopped")

    def view(self) -> View:
        parts = self._parts
        if self._busy is not None:
            status = self._busy
        elif self._finishing:
            status = Status.FINISHING
        elif parts is None:
            status = Status.IDLE
        else:
            status = self._status(parts)
        idle = status is Status.IDLE and not self._lock.locked()
        return View(
            status=status,
            speaking=bool(parts is not None and parts.speaking()),
            listened_seconds=self._listened_now(),
            error=self._error,
            can_start=idle,
            can_pause=status is Status.LISTENING,
            can_resume=status is Status.PAUSED,
            can_stop=status in (Status.LISTENING, Status.PAUSED, Status.INTERRUPTED),
            dialogue=self._dialogue,
            question=self._live_question if self._dialogue is Dialogue.HEARD else self._question,
            answer=self._answer,
        )

    def _clear_dialogue(self) -> None:
        self._dialogue = Dialogue.NONE
        self._question = ""
        self._answer = ""
        self._live_question = ""
        self._question_id = 0
        self._previous: tuple[Dialogue, str, str] = (Dialogue.NONE, "", "")

    def _status(self, parts: SessionParts) -> Status:
        state = parts.state()
        if state is SessionState.RUNNING:
            return Status.LISTENING if parts.streamer.forwarding else Status.INTERRUPTED
        if state is SessionState.PAUSED:
            # Capture can pause itself when a device fails; only a user pause can be resumed.
            return Status.PAUSED if self._paused_by_user else Status.INTERRUPTED
        return Status.INTERRUPTED

    def _listened_now(self) -> float:
        if self._running_since is None:
            return self._listened
        return self._listened + (self._clock() - self._running_since)

    def _settle_clock(self) -> None:
        if self._running_since is not None:
            self._listened += self._clock() - self._running_since
            self._running_since = None

    async def _teardown(self, parts: SessionParts) -> None:
        try:
            await parts.streamer.wait_playback()
        except Exception as error:  # noqa: BLE001 - cleanup must still run
            logger.warning("window playback wait failed error=%s", type(error).__name__)
        try:
            parts.close()
        except Exception as error:  # noqa: BLE001 - nothing more to release
            logger.warning("window cleanup failed error=%s", type(error).__name__)
