"""Segment C2: authenticated receiver. Audio only while running."""

from __future__ import annotations

import ssl
from datetime import datetime, timezone

import pytest

from orchestration.receive import (
    ReceiveServer,
    ReceiveSession,
    TokenVerifier,
    build_server_ssl_context,
    parse_control,
)


class FakeSink:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.chunks: list[bytes] = []
        self.paused = 0
        self.resumed = 0
        self.stopped = 0

    async def start(self, session_id: str) -> None:
        self.started.append(session_id)

    async def audio(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    async def pause(self, at: datetime) -> None:
        del at
        self.paused += 1

    async def resume(self, at: datetime) -> None:
        del at
        self.resumed += 1

    async def stop(self, at: datetime) -> None:
        del at
        self.stopped += 1


def _clock() -> datetime:
    return datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_running_audio_reaches_the_sink_and_pause_blocks_it() -> None:
    sink = FakeSink()
    session = ReceiveSession(sink, clock=_clock)
    assert (await session.handle('{"action":"start","session_id":"abc"}'))["ok"] is True
    await session.handle(b"\x01\x02")
    assert (await session.handle('{"action":"pause","session_id":"abc"}'))["ok"] is True
    await session.handle(b"\x03\x04")
    assert (await session.handle('{"action":"resume","session_id":"abc"}'))["ok"] is True
    await session.handle(b"\x05\x06")
    assert sink.chunks == [b"\x01\x02", b"\x05\x06"]
    assert sink.paused == 1
    assert sink.resumed == 1


@pytest.mark.asyncio
async def test_unknown_and_unaligned_audio_is_dropped() -> None:
    sink = FakeSink()
    session = ReceiveSession(sink, clock=_clock)
    await session.handle(b"\x01\x02")
    reply = await session.handle('{"action":"pause","session_id":"missing"}')
    assert reply == {"ok": False, "error": "unknown_session"}
    assert (await session.handle('{"action":"start","session_id":"abc"}'))["ok"] is True
    await session.handle(b"\x01")
    assert sink.chunks == []
    assert sink.started == ["abc"]


@pytest.mark.asyncio
async def test_stop_rejects_later_audio() -> None:
    sink = FakeSink()
    session = ReceiveSession(sink, clock=_clock)
    await session.handle('{"action":"start","session_id":"abc"}')
    assert (await session.handle('{"action":"stop","session_id":"abc"}'))["ok"] is True
    await session.handle(b"\x01\x02")
    assert sink.stopped == 1
    assert sink.chunks == []


def test_control_frame_rejects_extra_fields_and_bad_ids() -> None:
    with pytest.raises(ValueError):
        parse_control('{"action":"start","session_id":"abc","audio":"nope"}')
    with pytest.raises(ValueError):
        parse_control('{"action":"start","session_id":"has space"}')
    with pytest.raises(ValueError):
        parse_control("not json")


def test_token_verifier_fails_closed() -> None:
    verifier = TokenVerifier("expected-token")
    assert verifier.allow({"Authorization": "Bearer expected-token"}) is True
    assert verifier.allow({"authorization": "Bearer expected-token"}) is True
    assert verifier.allow({"Authorization": "Bearer wrong-token"}) is False
    assert verifier.allow({}) is False
    with pytest.raises(ValueError):
        TokenVerifier("")


def test_server_tls_context_requires_client_certs_for_mtls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def __init__(self, protocol: int) -> None:
            self.protocol = protocol
            self.minimum_version: ssl.TLSVersion | None = None
            self.verify_mode = ssl.CERT_NONE
            self.chain: tuple[str, str] | None = None
            self.ca: str | None = None

        def load_cert_chain(self, cert: str, key: str) -> None:
            self.chain = (cert, key)

        def load_verify_locations(self, path: str) -> None:
            self.ca = path

    monkeypatch.setattr(ssl, "SSLContext", FakeContext)
    mutual = build_server_ssl_context("server.pem", "server.key", "client-ca.pem")
    assert mutual.verify_mode == ssl.CERT_REQUIRED
    assert mutual.ca == "client-ca.pem"
    token_mode = build_server_ssl_context("server.pem", "server.key")
    assert token_mode.verify_mode == ssl.CERT_NONE
    with pytest.raises(ValueError):
        ReceiveServer(FakeSink(), TokenVerifier("t"), None)  # type: ignore[arg-type]
