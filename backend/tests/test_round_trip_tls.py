"""Sprint 5 round trip over local TLS, without Azure or Anthropic.

Real receiver, LiveSession, PlaybackHub, desktop BackendClient, PlaybackClient
and AudioPlayer. STT, orchestrator and TTS are fakes. Covers: answer in the
transcript and spoken, Pause omits a span, Stop is not held up by playback,
and Stop does not cut an answer that is still streaming.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from orchestration.live import LiveSession
from orchestration.playback import PlaybackHub
from orchestration.receive import ReceiveServer, TokenVerifier, build_server_ssl_context
from stt.phrase_detector import PhraseDetector
from stt.speech import SpeechToText
from stt.transcript import SessionTranscript
from tests.test_receive_tls import _write_cert
from tests.test_speech import MemoryAdapter

_DESKTOP = Path(__file__).resolve().parents[2] / "desktop-app"
if str(_DESKTOP) not in sys.path:
    sys.path.append(str(_DESKTOP))

from playback.client import PlaybackClient  # noqa: E402
from playback.player import AudioPlayer  # noqa: E402
from transport.client import BackendClient, TokenAuth, WebSocketConnector  # noqa: E402

_TOKEN = "local-test-token"
_ANSWER = "Q3 revenue was 12.4 billion rupees."
_TTS_CHUNKS = [b"\x01\x00" * 800, b"\x02\x00" * 800, b"\x03\x00" * 800]


class FakeOrchestrator:
    async def answer(self, transcript_slice: str, question: str) -> str:
        assert question == "what was revenue"
        return _ANSWER


class GatedTTS:
    """Streams the first chunk, then waits for `gate` before the rest."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def synthesize(self, text: str):
        assert text == _ANSWER
        yield _TTS_CHUNKS[0]
        self.started.set()
        await self.gate.wait()
        for chunk in _TTS_CHUNKS[1:]:
            yield chunk


class RecordingSink:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    def close(self) -> None:
        pass


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.fixture
async def stack(tmp_path: Path):
    cert, key = _write_cert(tmp_path)
    tts = GatedTTS()
    hub = PlaybackHub(tts)
    sessions: list[tuple[LiveSession, MemoryAdapter]] = []
    # Transcripts as handed to on_closed, just before Stop clears them from memory.
    finals: list[str] = []

    def new_session() -> LiveSession:
        adapter = MemoryAdapter()
        live = LiveSession(
            SpeechToText(adapter),
            SessionTranscript(),
            PhraseDetector("board assistant"),
            orchestrator=FakeOrchestrator(),
            delivery=hub,
            on_closed=lambda transcript: finals.append(transcript.render()),
        )
        sessions.append((live, adapter))
        return live

    server = ReceiveServer(
        None,
        TokenVerifier(_TOKEN),
        build_server_ssl_context(str(cert), str(key)),
        host="127.0.0.1",
        port=0,
        sink_factory=new_session,
        playback=hub,
    )
    await server.start()
    base = f"wss://127.0.0.1:{server.bound_port}"

    def connector(path: str = "", token: str = _TOKEN) -> WebSocketConnector:
        return WebSocketConnector(base + path, TokenAuth(token), server_ca_path=str(cert))

    try:
        yield {"connector": connector, "sessions": sessions, "finals": finals, "tts": tts, "hub": hub}
    finally:
        await server.stop()
        await hub.shutdown()


async def test_gate_round_trip_pause_and_stop_do_not_cut_the_answer(stack) -> None:
    connector = stack["connector"]
    tts: GatedTTS = stack["tts"]
    capture = BackendClient(connector())
    sink = RecordingSink()
    player = AudioPlayer(sink, tail_seconds=0)
    playback = PlaybackClient(connector("/playback"), player)

    await capture.start_session("session-1")
    playing = asyncio.create_task(playback.run("session-1"))
    live, adapter = stack["sessions"][0]
    await capture.send_audio(b"\x01\x02")
    adapter.emit("good morning everyone")
    adapter.emit("board assistant what was revenue")
    await _until(lambda: "Assistant:" in live.transcript.render())
    await asyncio.wait_for(tts.started.wait(), timeout=2)
    await _until(lambda: player.speaking)

    await capture.pause_session()
    adapter.emit("secret side conversation")
    await capture.resume_session()
    adapter.emit("next agenda item")

    began = time.monotonic()
    await capture.stop_session()
    assert time.monotonic() - began < 1.0, "Stop waited for playback"
    assert not playing.done(), "Stop cut the answer"

    tts.gate.set()
    await asyncio.wait_for(playing, timeout=3)
    await live.wait_closed()

    assert bytes(sink.data) == b"".join(_TTS_CHUNKS)
    assert playback.ended and playback.answers_played == 1
    [rendered] = stack["finals"]
    assert live.transcript.render() == ""
    assert f"Assistant: {_ANSWER}" in rendered
    assert "[paused " in rendered
    assert "secret side conversation" not in rendered
    assert "next agenda item" in rendered
    assert not player.speaking


async def _raw(connector, path: str, token: str = _TOKEN):
    socket = await connector(path, token).connect()
    return socket


async def test_playback_refuses_unknown_session_and_malformed_subscribe(stack) -> None:
    connector = stack["connector"]
    socket = await _raw(connector, "/playback")
    await socket.send(json.dumps({"action": "subscribe", "session_id": "nobody"}))
    assert json.loads(await socket.recv()) == {"ok": False, "error": "unknown_session"}
    await socket.close()

    socket = await _raw(connector, "/playback")
    await socket.send(json.dumps({"action": "subscribe", "session_id": "x", "extra": 1}))
    assert json.loads(await socket.recv()) == {"ok": False, "error": "bad_message"}
    await socket.close()


async def test_second_playback_socket_is_refused_and_bad_token_fails(stack) -> None:
    connector = stack["connector"]
    capture = BackendClient(connector())
    await capture.start_session("session-2")
    first = await _raw(connector, "/playback")
    await first.send(json.dumps({"action": "subscribe", "session_id": "session-2"}))
    assert json.loads(await first.recv()) == {"ok": True}
    second = await _raw(connector, "/playback")
    await second.send(json.dumps({"action": "subscribe", "session_id": "session-2"}))
    assert json.loads(await second.recv()) == {"ok": False, "error": "unknown_session"}
    await second.close()
    with pytest.raises(Exception):
        await _raw(connector, "/playback", token="wrong")
    with pytest.raises(Exception):
        await _raw(connector, "/elsewhere")
    await capture.stop_session()
    assert json.loads(await first.recv()) == {"type": "session_end"}
    await first.close()


async def test_capture_socket_dropped_without_stop_stops_the_session(stack) -> None:
    connector = stack["connector"]
    capture = BackendClient(connector())
    await capture.start_session("session-3")
    live, adapter = stack["sessions"][-1]
    await capture._close_socket()  # simulate a crash: no Stop frame
    await _until(lambda: adapter.stop_calls == 1)
    await live.wait_closed()
    # The hub removes the channel in its own task after close(); wait for it.
    await asyncio.wait_for(stack["hub"].wait_closed("session-3"), timeout=5)
    assert not stack["hub"].is_open("session-3")
