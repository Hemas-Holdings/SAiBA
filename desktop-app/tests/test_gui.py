"""Desktop window: the Qt bridge mirrors the controls, and the QML loads cleanly.

Needs the `gui` extra (PySide6-Essentials). The QML check renders off-screen
and is skipped where Qt Quick's graphics libraries are not available.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from tests.test_controls import Clock, FakeStreamer  # noqa: E402
from ui.controls import SessionControls  # noqa: E402
from ui.gui import QML_PATH, SessionBridge  # noqa: E402


@pytest.fixture(scope="module")
def qt_app() -> QCoreApplication:
    existing = QCoreApplication.instance()
    if existing is not None:
        return existing
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QGuiApplication

        return QGuiApplication([])
    except ImportError:
        return QCoreApplication([])


async def _settle(bridge: SessionBridge) -> None:
    for _ in range(20):
        await asyncio.sleep(0)
    bridge.refresh()


def _bridge(streamer: FakeStreamer, clock: Clock | None = None, on_closed=None) -> SessionBridge:
    controls = SessionControls(lambda: streamer.parts(), clock or Clock())
    return SessionBridge(controls, on_closed=on_closed)


async def test_bridge_mirrors_the_session(qt_app: QCoreApplication) -> None:
    clock = Clock()
    streamer = FakeStreamer()
    bridge = _bridge(streamer, clock)
    assert bridge.status == "idle"
    assert bridge.canStart and not bridge.canStop
    assert bridge.elapsed == "00:00:00"

    bridge.start()
    await _settle(bridge)
    assert bridge.status == "listening"
    assert bridge.canPause and bridge.canStop and not bridge.canStart

    clock.now += 3725
    bridge.refresh()
    assert bridge.elapsed == "01:02:05"

    streamer.speaking = True
    bridge.refresh()
    assert bridge.speaking

    bridge.pause()
    await _settle(bridge)
    assert bridge.status == "paused" and bridge.canResume
    bridge.resume()
    await _settle(bridge)
    assert bridge.status == "listening"
    bridge.stop()
    await _settle(bridge)
    assert bridge.status == "idle"
    assert streamer.calls == ["start", "pause", "resume", "stop", "wait_playback"]


async def test_changed_fires_only_when_the_view_changes(qt_app: QCoreApplication) -> None:
    streamer = FakeStreamer()
    bridge = _bridge(streamer)
    seen: list[int] = []
    bridge.changed.connect(lambda: seen.append(1))
    bridge.refresh()
    assert seen == []
    bridge.start()
    await _settle(bridge)
    assert seen
    count = len(seen)
    bridge.refresh()
    assert len(seen) == count


async def test_errors_show_and_dismiss(qt_app: QCoreApplication) -> None:
    streamer = FakeStreamer(fail_on="start")
    bridge = _bridge(streamer)
    bridge.start()
    await _settle(bridge)
    assert bridge.error == "Could not start (RuntimeError)."
    bridge.dismissError()
    assert bridge.error == ""


async def test_close_is_immediate_when_idle(qt_app: QCoreApplication) -> None:
    bridge = _bridge(FakeStreamer())
    assert bridge.requestClose() is True
    assert bridge.closing is False


async def test_close_while_active_stops_first_then_closes(qt_app: QCoreApplication) -> None:
    closed: list[int] = []
    streamer = FakeStreamer()
    bridge = _bridge(streamer, on_closed=lambda: closed.append(1))
    bridge.start()
    await _settle(bridge)
    streamer.playback_done.clear()
    assert bridge.requestClose() is False
    assert bridge.requestClose() is False
    await _settle(bridge)
    assert bridge.closing
    assert closed == []
    streamer.playback_done.set()
    for _ in range(10):
        await asyncio.sleep(0.06)
        if closed:
            break
    assert closed == [1]
    assert streamer.calls.count("stop") == 1


def test_window_qml_loads_without_warnings(qt_app: QCoreApplication) -> None:
    pytest.importorskip("PySide6.QtQuick")
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    if not isinstance(qt_app, QGuiApplication):
        pytest.skip("Qt GUI application not available")
    bridge = _bridge(FakeStreamer())
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
    engine.rootContext().setContextProperty("session", bridge)
    engine.load(str(QML_PATH))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    for _ in range(5):
        qt_app.processEvents()
    assert warnings == []
    window.close()


def test_qml_loads_nothing_from_the_network() -> None:
    source = QML_PATH.read_text(encoding="utf-8")
    assert "http://" not in source and "https://" not in source
    assert "XMLHttpRequest" not in source
