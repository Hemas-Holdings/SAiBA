"""Local console: commands are local, config is validated, mic-only silence source."""

from __future__ import annotations

import pytest

from ui.console import Console, ConfigError, SilenceBackend, load_config, playback_url


class FakeStreamer:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    async def _do(self, name: str) -> None:
        self.calls.append(name)
        if name == self._fail_on:
            raise RuntimeError("boom")

    async def start(self):
        await self._do("start")

    async def pause(self):
        await self._do("pause")

    async def resume(self):
        await self._do("resume")

    async def stop(self):
        await self._do("stop")

    async def wait_playback(self):
        await self._do("wait_playback")


def _lines(*lines: str):
    items = list(lines)

    def read() -> str:
        if not items:
            raise EOFError
        return items.pop(0)

    return read


async def test_commands_drive_the_streamer_and_quit_waits_for_playback() -> None:
    streamer = FakeStreamer()
    out: list[str] = []
    await Console(streamer, _lines("p", "s", "p", "r", "x", "q"), out.append).run()
    assert streamer.calls == ["start", "pause", "resume", "stop", "wait_playback"]
    assert out[0].startswith("commands:")


async def test_eof_quits_and_quit_before_start_does_nothing() -> None:
    streamer = FakeStreamer()
    await Console(streamer, _lines(), lambda _m: None).run()
    assert streamer.calls == []


async def test_failed_command_keeps_the_console_running() -> None:
    streamer = FakeStreamer(fail_on="start")
    out: list[str] = []
    await Console(streamer, _lines("s", "q"), out.append).run()
    assert "command failed: RuntimeError" in out


def test_config_requires_token_mode_and_names_missing_settings() -> None:
    with pytest.raises(ConfigError, match="BACKEND_BASE_URL"):
        load_config({"BACKEND_AUTH_TOKEN": "secret-token"})
    with pytest.raises(ConfigError, match="token only"):
        load_config({"BACKEND_BASE_URL": "wss://h", "BACKEND_AUTH_TOKEN": "t", "BACKEND_AUTH_MODE": "mtls"})
    with pytest.raises(ConfigError):
        load_config({"BACKEND_BASE_URL": "wss://h", "BACKEND_AUTH_TOKEN": "t", "STREAM_BUFFER_SECONDS": "x"})
    config = load_config({"BACKEND_BASE_URL": "wss://h:8443/", "BACKEND_AUTH_TOKEN": "t"})
    assert config.base_url == "wss://h:8443"
    assert playback_url(config.base_url) == "wss://h:8443/playback"
    assert config.buffer_seconds == 8


def test_silence_backend_paces_and_returns_zeros() -> None:
    backend = SilenceBackend()
    backend.open()
    assert backend.read(16) == b"\x00" * 32
    backend.close()
