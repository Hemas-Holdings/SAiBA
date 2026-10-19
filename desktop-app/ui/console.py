"""Local console controls for a session (the tray icon comes later).

Commands are read from this machine's keyboard only: `s` Start, `p` Pause,
`r` Resume, `q` Stop and quit, each followed by Enter. Nothing here listens
on the network. Answers play through the local output device; capture is
muted while one plays. Pause and Stop do not cut an answer that is playing.
No audio or transcript is written to disk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from capture.audio_format import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH

logger = logging.getLogger(__name__)

HELP = "commands: s = start, p = pause, r = resume, q = stop and quit"


class ConfigError(Exception):
    """Required settings are missing. The message names them, never their values."""


@dataclass(frozen=True)
class ConsoleConfig:
    base_url: str
    token: str
    server_ca_path: str | None
    buffer_seconds: float


def load_config(env: Mapping[str, str]) -> ConsoleConfig:
    missing = [name for name in ("BACKEND_BASE_URL", "BACKEND_AUTH_TOKEN") if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("missing settings: " + ", ".join(missing))
    mode = (env.get("BACKEND_AUTH_MODE") or "token").strip().lower()
    if mode != "token":
        raise ConfigError("the console driver supports BACKEND_AUTH_MODE=token only")
    try:
        buffer_seconds = float(env.get("STREAM_BUFFER_SECONDS") or 8)
    except ValueError as exc:
        raise ConfigError("STREAM_BUFFER_SECONDS must be a number") from exc
    return ConsoleConfig(
        base_url=env["BACKEND_BASE_URL"].strip().rstrip("/"),
        token=env["BACKEND_AUTH_TOKEN"].strip(),
        server_ca_path=(env.get("TLS_CA_CERT_PATH") or "").strip() or None,
        buffer_seconds=buffer_seconds,
    )


def playback_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/playback"


class SilenceBackend:
    """Stands in for loopback in `--mic-only` mode: real-time silence, no device."""

    sample_rate = SAMPLE_RATE
    channels = CHANNELS
    sample_width = SAMPLE_WIDTH

    def open(self) -> None:
        pass

    def read(self, frame_count: int) -> bytes:
        time.sleep(frame_count / self.sample_rate)
        return b"\x00" * (frame_count * self.sample_width * self.channels)

    def close(self) -> None:
        pass


class Console:
    """Runs local commands against a streamer. Input comes from `read_line`."""

    def __init__(self, streamer: object, read_line: Callable[[], str], write: Callable[[str], None] = print) -> None:
        self._streamer = streamer
        self._read_line = read_line
        self._write = write

    async def run(self) -> None:
        self._write(HELP)
        started = False
        while True:
            try:
                line = await asyncio.to_thread(self._read_line)
            except EOFError:
                line = "q"
            command = line.strip().lower()
            try:
                if command == "s" and not started:
                    await self._streamer.start()  # type: ignore[attr-defined]
                    started = True
                    self._write("started")
                elif command == "p" and started:
                    await self._streamer.pause()  # type: ignore[attr-defined]
                    self._write("paused: nothing is captured until resume")
                elif command == "r" and started:
                    await self._streamer.resume()  # type: ignore[attr-defined]
                    self._write("resumed")
                elif command == "q":
                    if started:
                        await self._streamer.stop()  # type: ignore[attr-defined]
                        self._write("stopped; finishing any answer that is playing")
                        await self._streamer.wait_playback()  # type: ignore[attr-defined]
                    self._write("done")
                    return
                else:
                    self._write(HELP)
            except Exception as error:  # noqa: BLE001 - report and keep the console usable
                logger.warning("command failed error=%s", type(error).__name__)
                self._write(f"command failed: {type(error).__name__}")


def build_streamer(config: ConsoleConfig, mic_only: bool = False) -> tuple[object, Callable[[], None]]:
    """Wire capture, transport and playback. Returns the streamer and a cleanup."""

    from capture.listen import SoundDevicePlaybackSink
    from capture.loopback import LoopbackCapture
    from capture.microphone import MicCapture
    from capture.mixer import AudioMixer
    from playback.client import PlaybackClient
    from playback.player import AudioPlayer
    from session.buffer import StreamBuffer
    from session.controller import SessionController
    from transport.client import BackendClient, TokenAuth, WebSocketConnector
    from transport.streamer import SessionStreamer

    auth = TokenAuth(config.token)
    loopback = LoopbackCapture(SilenceBackend()) if mic_only else LoopbackCapture()
    controller = SessionController(loopback, MicCapture(), AudioMixer(), StreamBuffer(config.buffer_seconds))
    client = BackendClient(WebSocketConnector(config.base_url, auth, config.server_ca_path))
    player = AudioPlayer(SoundDevicePlaybackSink())
    playback = PlaybackClient(WebSocketConnector(playback_url(config.base_url), auth, config.server_ca_path), player)
    streamer = SessionStreamer(controller, client, playback=playback, muted=lambda: player.speaking)
    return streamer, player.close
