"""Desktop window for local Start / Pause / Resume / Stop.

    python -m ui.gui [--env-file .env] [--mic-only]

A Qt Quick window over `ui.controls.SessionControls`. Buttons are the only
input, from this machine; nothing here listens on the network, and the QML
loads no remote resources. The window shows session state, listening time,
whether an answer is playing, and the latest question and answer (from the
backend's window events, after the trigger phrase only). It never receives
audio or any other meeting text, and logs and saves no text. Closing the
window while a session is active stops it and lets an answer that is
already playing finish first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

from ui.controls import SessionControls, SessionParts, View

logger = logging.getLogger(__name__)

QML_PATH = Path(__file__).resolve().parent / "qml" / "Main.qml"
_TICK_MS = 100


class SessionBridge(QObject):
    """Exposes the controls to QML. Holds no audio and no transcript."""

    changed = Signal()

    def __init__(self, controls: SessionControls, on_closed: Any = None) -> None:
        super().__init__()
        self._controls = controls
        self._on_closed = on_closed
        self._view: View = controls.view()
        self._closing = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    @Slot()
    def refresh(self) -> None:
        view = self._controls.view()
        if view != self._view:
            self._view = view
            self.changed.emit()

    def _run(self, coroutine: Any) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _task: self.refresh())
        self.refresh()

    @Slot()
    def start(self) -> None:
        self._run(self._controls.start())

    @Slot()
    def pause(self) -> None:
        self._run(self._controls.pause())

    @Slot()
    def resume(self) -> None:
        self._run(self._controls.resume())

    @Slot()
    def stop(self) -> None:
        self._run(self._controls.stop())

    @Slot()
    def dismissError(self) -> None:
        self._controls.clear_error()
        self.refresh()

    @Slot(result=bool)
    def requestClose(self) -> bool:
        """True if the window may close now; otherwise stop first, then close."""

        if not self._controls.active:
            return True
        if not self._closing:
            self._closing = True
            self._run(self._stop_then_close())
        return False

    async def _stop_then_close(self) -> None:
        await self._controls.stop()
        while self._controls.active:
            await asyncio.sleep(0.05)
        if self._on_closed is not None:
            self._on_closed()

    def _status(self) -> str:
        return self._view.status.value

    def _speaking(self) -> bool:
        return self._view.speaking

    def _elapsed(self) -> str:
        total = int(self._view.listened_seconds)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _error(self) -> str:
        return self._view.error

    def _can_start(self) -> bool:
        return self._view.can_start

    def _can_pause(self) -> bool:
        return self._view.can_pause

    def _can_resume(self) -> bool:
        return self._view.can_resume

    def _can_stop(self) -> bool:
        return self._view.can_stop

    def _is_closing(self) -> bool:
        return self._closing

    def _dialogue(self) -> str:
        return self._view.dialogue.value

    def _question(self) -> str:
        return self._view.question

    def _answer(self) -> str:
        return self._view.answer

    status = Property(str, _status, notify=changed)
    speaking = Property(bool, _speaking, notify=changed)
    elapsed = Property(str, _elapsed, notify=changed)
    error = Property(str, _error, notify=changed)
    canStart = Property(bool, _can_start, notify=changed)
    canPause = Property(bool, _can_pause, notify=changed)
    canResume = Property(bool, _can_resume, notify=changed)
    canStop = Property(bool, _can_stop, notify=changed)
    closing = Property(bool, _is_closing, notify=changed)
    dialogue = Property(str, _dialogue, notify=changed)
    question = Property(str, _question, notify=changed)
    answer = Property(str, _answer, notify=changed)


def build_parts(config: Any, mic_only: bool, on_event: Any = None) -> SessionParts:
    from ui.console import wire_session

    streamer, controller, player = wire_session(config, mic_only, on_event=on_event)
    return SessionParts(
        streamer=streamer,
        state=lambda: controller.state,
        speaking=lambda: player.speaking,
        close=player.close,
    )


def main(argv: list[str] | None = None) -> int:
    from ui.__main__ import _read_env_file
    from ui.console import ConfigError, load_config

    parser = argparse.ArgumentParser(description="Board Knowledge Assistant window")
    parser.add_argument("--env-file", type=Path, help="Read settings from this file before the environment")
    parser.add_argument("--mic-only", action="store_true", help="Capture the microphone only (no WASAPI loopback)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env: dict[str, str] = {}
    if args.env_file is not None:
        env.update(_read_env_file(args.env_file))
    env.update({key: value for key, value in os.environ.items() if value})
    try:
        config = load_config(env)
    except ConfigError as error:
        logging.error("%s", error)
        return 2
    if sys.platform != "win32" and not args.mic_only:
        logging.error("Live WASAPI loopback requires Windows. Use --mic-only on this platform.")
        return 1

    import qasync
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Board Assistant")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    controls: SessionControls

    def on_event(event: dict[str, object]) -> None:
        controls.handle_event(event)
        bridge.refresh()

    controls = SessionControls(lambda: build_parts(config, args.mic_only, on_event=on_event))
    bridge = SessionBridge(controls, on_closed=app.quit)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("session", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    if not engine.rootObjects():
        logging.error("window failed to load")
        return 1

    closed = asyncio.Event()
    app.aboutToQuit.connect(closed.set)
    with loop:
        loop.run_until_complete(closed.wait())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
