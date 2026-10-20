"""Local TLS check: desktop client against the real receiver. Azure is not used."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orchestration.receive import ReceiveServer, TokenVerifier, build_server_ssl_context
from tests.devcert import write_self_signed
from tests.test_receive import FakeSink

_DESKTOP = Path(__file__).resolve().parents[2] / "desktop-app"
if str(_DESKTOP) not in sys.path:
    sys.path.append(str(_DESKTOP))

from transport.client import BackendClient, TokenAuth, TransportError, WebSocketConnector  # noqa: E402


def _write_cert(directory: Path) -> tuple[Path, Path]:
    return write_self_signed(directory)


@pytest.mark.asyncio
async def test_token_mode_round_trip_and_wrong_token_fails(tmp_path: Path) -> None:
    cert, key = _write_cert(tmp_path)
    server = ReceiveServer(
        FakeSink(),
        TokenVerifier("local-test-token"),
        build_server_ssl_context(str(cert), str(key)),
        host="127.0.0.1",
        port=0,
    )
    sink = server._sink
    await server.start()
    try:
        client = BackendClient(
            WebSocketConnector(
                f"wss://127.0.0.1:{server.bound_port}",
                TokenAuth("local-test-token"),
                server_ca_path=str(cert),
            )
        )
        await client.start_session("session-1")
        await client.send_audio(b"\x01\x02")
        await client.pause_session()
        await client.send_audio(b"\x03\x04")
        await client.stop_session()
        assert sink.chunks == [b"\x01\x02"]
        assert sink.paused == 1

        rejected = BackendClient(
            WebSocketConnector(
                f"wss://127.0.0.1:{server.bound_port}",
                TokenAuth("wrong-token"),
                server_ca_path=str(cert),
            )
        )
        with pytest.raises(TransportError, match="handshake failed"):
            await rejected.start_session("session-2")
        assert sink.chunks == [b"\x01\x02"]
    finally:
        await server.stop()
