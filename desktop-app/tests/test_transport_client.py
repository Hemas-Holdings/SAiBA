"""Segment C1: TLS client. Pause and failures do not flush audio."""

from __future__ import annotations

import json
import logging
import ssl

import pytest

from transport.client import (
    BackendClient,
    MutualTlsAuth,
    TokenAuth,
    TransportError,
    WebSocketConnector,
    build_client_ssl_context,
)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self.fail_on: bytes | None = None

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, bytes) and data == self.fail_on:
            raise ConnectionError("broken pipe")
        self.sent.append(data)

    async def recv(self) -> str:
        return '{"ok":true}'

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, socket: FakeSocket, failures: int = 0) -> None:
        self.socket = socket
        self.failures = failures
        self.calls = 0

    async def connect(self) -> FakeSocket:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("handshake refused")
        return self.socket


def _client(failures: int = 0) -> tuple[BackendClient, FakeSocket, FakeConnector]:
    socket = FakeSocket()
    connector = FakeConnector(socket, failures=failures)
    return BackendClient(connector), socket, connector


@pytest.mark.asyncio
async def test_start_sends_control_and_audio_without_logging_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, socket, _connector = _client()
    token_marker = "TOKEN-DO-NOT-LOG-99"
    pcm = b"\xab\xcd" * 4
    with caplog.at_level(logging.DEBUG):
        await client.start_session("session-1")
        await client.send_audio(pcm)
    control = json.loads(socket.sent[0])
    assert control == {"action": "start", "session_id": "session-1"}
    assert socket.sent[1] == pcm
    assert token_marker not in caplog.text
    assert "\\xab" not in caplog.text
    for record in caplog.records:
        assert not any(isinstance(arg, (bytes, bytearray)) for arg in record.args)


@pytest.mark.asyncio
async def test_pause_sends_no_further_audio() -> None:
    client, socket, _connector = _client()
    await client.start_session("session-1")
    await client.send_audio(b"\x01\x02")
    await client.pause_session()
    await client.send_audio(b"\x03\x04")
    binary = [frame for frame in socket.sent if isinstance(frame, bytes)]
    assert binary == [b"\x01\x02"]
    assert json.loads(socket.sent[-1])["action"] == "pause"
    assert client.accepting_audio is False


@pytest.mark.asyncio
async def test_send_failure_does_not_flush_later_audio() -> None:
    client, socket, _connector = _client()
    await client.start_session("session-1")
    await client.send_audio(b"\x01\x00")
    socket.fail_on = b"\x02\x00"
    with pytest.raises(TransportError, match="audio send failed"):
        await client.send_audio(b"\x02\x00")
    await client.send_audio(b"\x03\x00")
    binary = [frame for frame in socket.sent if isinstance(frame, bytes)]
    assert binary == [b"\x01\x00"]
    assert b"\x02\x00" not in binary
    assert b"\x03\x00" not in binary


@pytest.mark.asyncio
async def test_handshake_retries_then_connects_without_sending_audio() -> None:
    client, socket, connector = _client(failures=2)
    await client.start_session("session-1")
    assert connector.calls == 3
    assert socket.sent == ['{"action":"start","session_id":"session-1"}']


@pytest.mark.asyncio
async def test_handshake_failure_raises_and_sends_nothing() -> None:
    client, socket, connector = _client(failures=3)
    with pytest.raises(TransportError, match="handshake failed"):
        await client.start_session("session-1")
    assert connector.calls == 3
    assert socket.sent == []


@pytest.mark.asyncio
async def test_rejected_control_fails_closed() -> None:
    socket = FakeSocket()

    async def reject(self: FakeSocket) -> str:
        del self
        return '{"ok":false,"error":"rejected"}'

    socket.recv = reject.__get__(socket, FakeSocket)  # type: ignore[method-assign]
    client = BackendClient(FakeConnector(socket))
    with pytest.raises(TransportError, match="control rejected"):
        await client.start_session("session-1")
    await client.send_audio(b"\x01\x02")
    assert socket.sent == ['{"action":"start","session_id":"session-1"}']


@pytest.mark.asyncio
async def test_resume_allows_audio_again() -> None:
    client, socket, _connector = _client()
    await client.start_session("session-1")
    await client.pause_session()
    await client.resume_session()
    await client.send_audio(b"\x05\x06")
    assert socket.sent[-1] == b"\x05\x06"


@pytest.mark.asyncio
async def test_stop_closes_and_drops_later_audio() -> None:
    client, socket, _connector = _client()
    await client.start_session("session-1")
    await client.stop_session()
    await client.send_audio(b"\x07\x08")
    assert socket.closed is True
    assert b"\x07\x08" not in socket.sent
    assert json.loads([frame for frame in socket.sent if isinstance(frame, str)][-1])["action"] == "stop"


def test_token_header_and_wss_only() -> None:
    auth = TokenAuth("scoped-token")
    connector = WebSocketConnector("wss://board.example/session", auth)
    assert connector.headers() == {"Authorization": "Bearer scoped-token"}
    with pytest.raises(ValueError, match="wss"):
        WebSocketConnector("ws://board.example/session", auth)
    with pytest.raises(ValueError, match="non-empty"):
        TokenAuth("")


def test_mtls_context_loads_client_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.minimum_version: ssl.TLSVersion | None = None
            self.ca: str | None = None
            self.chain: tuple[str, str] | None = None

        def load_verify_locations(self, path: str) -> None:
            self.ca = path

        def load_cert_chain(self, cert: str, key: str) -> None:
            self.chain = (cert, key)

    def factory(*_args: object, **_kwargs: object) -> FakeContext:
        return FakeContext()

    monkeypatch.setattr(ssl, "create_default_context", factory)
    context = build_client_ssl_context(MutualTlsAuth("ca.pem", "client.pem", "client.key"))
    assert context.ca == "ca.pem"
    assert context.chain == ("client.pem", "client.key")
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.asyncio
async def test_session_id_is_restricted() -> None:
    client, _socket, _connector = _client()
    with pytest.raises(ValueError):
        await client.start_session("has space")
