"""TLS receiving endpoint for session control and mixed PCM.

Accepts a WebSocket only with a server certificate. Token mode checks a
bearer token; mutual TLS requires a client certificate at the TLS layer.
Binary frames are accepted only while that session is running. They are
handed to an injected sink and are not written to disk or logged.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import ssl
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8443
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ACTIONS = {"start", "pause", "resume", "stop"}


class SessionSink(Protocol):
    """Where accepted audio and session control go. Not a file."""

    async def start(self, session_id: str) -> None:
        """Begin recognition for this session."""

    async def audio(self, chunk: bytes) -> None:
        """Accept one PCM chunk."""

    async def pause(self, at: datetime) -> None:
        """Pause recognition."""

    async def resume(self, at: datetime) -> None:
        """Resume recognition."""

    async def stop(self, at: datetime) -> None:
        """End recognition."""


class Phase(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class TokenVerifier:
    """Constant-time bearer check. An empty token is refused at construction."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self._token = token

    def allow(self, headers: Mapping[str, str]) -> bool:
        presented = _bearer_token(headers)
        if presented is None:
            return False
        return secrets.compare_digest(presented, self._token)


def build_server_ssl_context(
    cert_path: str,
    key_path: str,
    client_ca_path: str | None = None,
) -> ssl.SSLContext:
    """Server TLS context. A client CA turns on mutual TLS."""

    if not cert_path or not key_path:
        raise ValueError("server certificate and key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    if client_ca_path:
        context.load_verify_locations(client_ca_path)
        context.verify_mode = ssl.CERT_REQUIRED
    else:
        context.verify_mode = ssl.CERT_NONE
    return context


class ReceiveSession:
    """One connection's session state. Audio is dropped unless running."""

    def __init__(
        self,
        sink: SessionSink,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sink = sink
        self._clock = clock if clock is not None else _utc_now
        self._phase = Phase.IDLE
        self._session_id: str | None = None

    @property
    def phase(self) -> Phase:
        return self._phase

    async def handle(self, message: str | bytes) -> dict[str, object] | None:
        """Apply one frame. Text frames return a JSON object. Audio returns None."""

        if isinstance(message, bytes):
            await self._audio(message)
            return None
        return await self._control(message)

    async def _audio(self, chunk: bytes) -> None:
        if self._phase is not Phase.RUNNING or not chunk:
            logger.info("audio rejected phase=%s", self._phase.value)
            return
        if len(chunk) % 2 != 0:
            logger.info("audio rejected unaligned")
            return
        await self._sink.audio(chunk)

    async def _control(self, text: str) -> dict[str, object]:
        try:
            action, session_id = parse_control(text)
        except ValueError:
            logger.info("control rejected")
            return {"ok": False, "error": "bad_message"}
        if action == "start":
            return await self._start(session_id)
        if self._session_id != session_id:
            logger.info("control rejected unknown session")
            return {"ok": False, "error": "unknown_session"}
        if action == "pause":
            return await self._pause()
        if action == "resume":
            return await self._resume()
        return await self._stop()

    async def _start(self, session_id: str) -> dict[str, object]:
        if self._phase is not Phase.IDLE:
            return {"ok": False, "error": "rejected"}
        self._session_id = session_id
        self._phase = Phase.RUNNING
        await self._sink.start(session_id)
        logger.info("receive session started")
        return {"ok": True}

    async def _pause(self) -> dict[str, object]:
        if self._phase is not Phase.RUNNING:
            return {"ok": False, "error": "not_running"}
        self._phase = Phase.PAUSED
        await self._sink.pause(self._clock())
        logger.info("receive session paused")
        return {"ok": True}

    async def _resume(self) -> dict[str, object]:
        if self._phase is not Phase.PAUSED:
            return {"ok": False, "error": "not_running"}
        self._phase = Phase.RUNNING
        await self._sink.resume(self._clock())
        logger.info("receive session resumed")
        return {"ok": True}

    async def _stop(self) -> dict[str, object]:
        if self._phase is Phase.STOPPED:
            return {"ok": False, "error": "rejected"}
        self._phase = Phase.STOPPED
        await self._sink.stop(self._clock())
        logger.info("receive session stopped")
        return {"ok": True}


class ReceiveServer:
    """WebSocket server. `ssl_context` is required; plaintext is refused."""

    def __init__(
        self,
        sink: SessionSink,
        verifier: TokenVerifier,
        ssl_context: ssl.SSLContext,
        host: str = DEFAULT_BIND_HOST,
        port: int = DEFAULT_BIND_PORT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ssl_context is None:
            raise ValueError("TLS context is required")
        self._sink = sink
        self._verifier = verifier
        self._ssl_context = ssl_context
        self._host = host
        self._port = port
        self._clock = clock
        self._server: object | None = None
        self.bound_port = port

    async def start(self) -> None:
        import websockets

        self._server = await websockets.serve(
            self._handler,
            self._host,
            self._port,
            ssl=self._ssl_context,
            process_request=self._process_request,
        )
        sockets = getattr(self._server, "sockets", None) or []
        if sockets:
            self.bound_port = int(sockets[0].getsockname()[1])
        logger.info("receive server listening")

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()  # type: ignore[attr-defined]
        await server.wait_closed()  # type: ignore[attr-defined]

    def _process_request(self, connection: object, request: object) -> object | None:
        headers = getattr(request, "headers", {})
        if self._verifier.allow(headers):
            return None
        logger.info("handshake rejected")
        respond = getattr(connection, "respond", None)
        if respond is None:
            return None
        return respond(401, "Unauthorized")

    async def _handler(self, connection: object) -> None:
        session = ReceiveSession(self._sink, self._clock)
        async for message in connection:  # type: ignore[attr-defined]
            try:
                reply = await session.handle(message)
            except Exception:
                logger.warning("receive frame failed closed")
                break
            if reply is None:
                continue
            await connection.send(json.dumps(reply, separators=(",", ":")))  # type: ignore[attr-defined]


def parse_control(text: str) -> tuple[str, str]:
    """Return action and session id. Extra fields are rejected."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("control frame is not json") from exc
    if not isinstance(payload, dict):
        raise ValueError("control frame is not an object")
    if set(payload) != {"action", "session_id"}:
        raise ValueError("control frame has unexpected fields")
    action = payload["action"]
    session_id = payload["session_id"]
    if action not in _ACTIONS or not isinstance(action, str):
        raise ValueError("control action is not allowed")
    if not isinstance(session_id, str) or _SESSION_ID.fullmatch(session_id) is None:
        raise ValueError("session id is not allowed")
    return action, session_id


def _bearer_token(headers: Mapping[str, str]) -> str | None:
    raw = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            raw = value
            break
    if raw is None or not raw.startswith("Bearer "):
        return None
    token = raw[len("Bearer ") :]
    if not token:
        return None
    return token


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
