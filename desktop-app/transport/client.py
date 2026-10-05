"""Authenticated TLS stream of session control and mixed PCM.

Sends Start / Pause / Resume / Stop and canonical PCM to the private
backend. Audio is sent only while the session is running. A send failure
drops the unsent chunk and stops further sends. Nothing is queued for a
later flush, and nothing is written to disk or logged. See
docs/session-model.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

_ACTIONS = ("start", "pause", "resume", "stop")
_HANDSHAKE_ATTEMPTS = 3


class TransportError(Exception):
    """The stream failed closed. Unsent audio is not retried."""


class TransportSocket(Protocol):
    """One TLS socket. Implementations must not log payloads."""

    async def send(self, data: str | bytes) -> None:
        """Send one text control frame or one binary PCM frame."""

    async def recv(self) -> str | bytes:
        """Receive the next frame."""

    async def close(self) -> None:
        """Close the socket."""


class SocketConnector(Protocol):
    """Opens the TLS socket. Handshake only; it does not send audio."""

    async def connect(self) -> TransportSocket:
        """Return an open socket."""


@dataclass(frozen=True)
class TokenAuth:
    """Scoped bearer token. Compared by the server, never logged here."""

    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token must be non-empty")


@dataclass(frozen=True)
class MutualTlsAuth:
    """Client certificate for mutual TLS."""

    ca_cert_path: str
    client_cert_path: str
    client_key_path: str

    def __post_init__(self) -> None:
        if not self.ca_cert_path or not self.client_cert_path or not self.client_key_path:
            raise ValueError("mutual TLS requires a CA, client certificate, and key")


def build_client_ssl_context(
    auth: TokenAuth | MutualTlsAuth,
    server_ca_path: str | None = None,
) -> ssl.SSLContext:
    """TLS client context. Token mode verifies the server. mTLS also presents a cert."""

    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if isinstance(auth, MutualTlsAuth):
        context.load_verify_locations(auth.ca_cert_path)
        context.load_cert_chain(auth.client_cert_path, auth.client_key_path)
    elif server_ca_path:
        context.load_verify_locations(server_ca_path)
    return context


class WebSocketConnector:
    """Connect with `wss`. Refuses plaintext `ws` URLs."""

    def __init__(
        self,
        url: str,
        auth: TokenAuth | MutualTlsAuth,
        server_ca_path: str | None = None,
    ) -> None:
        if not url.startswith("wss://"):
            raise ValueError("transport URL must use wss")
        self._url = url
        self._auth = auth
        self._server_ca_path = server_ca_path

    def ssl_context(self) -> ssl.SSLContext:
        return build_client_ssl_context(self._auth, self._server_ca_path)

    def headers(self) -> dict[str, str]:
        if isinstance(self._auth, TokenAuth):
            return {"Authorization": f"Bearer {self._auth.token}"}
        return {}

    async def connect(self) -> TransportSocket:
        import websockets

        connection = await websockets.connect(
            self._url,
            ssl=self.ssl_context(),
            additional_headers=self.headers(),
        )
        return _WebSocketSocket(connection)


class _WebSocketSocket:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def send(self, data: str | bytes) -> None:
        await self._connection.send(data)  # type: ignore[attr-defined]

    async def recv(self) -> str | bytes:
        message = await self._connection.recv()  # type: ignore[attr-defined]
        if isinstance(message, bytes):
            return message
        return str(message)

    async def close(self) -> None:
        await self._connection.close()  # type: ignore[attr-defined]


class BackendClient:
    """Session control plus PCM. PCM is never queued across failures or pause."""

    def __init__(
        self,
        connector: SocketConnector,
        handshake_attempts: int = _HANDSHAKE_ATTEMPTS,
    ) -> None:
        if handshake_attempts < 1:
            raise ValueError("handshake_attempts must be positive")
        self._connector = connector
        self._handshake_attempts = handshake_attempts
        self._socket: TransportSocket | None = None
        self._accepting_audio = False
        self._failed = False
        self._session_id: str | None = None
        self._send_lock = asyncio.Lock()

    @property
    def accepting_audio(self) -> bool:
        return self._accepting_audio and not self._failed

    async def start_session(self, session_id: str) -> None:
        """Open the socket if needed and mark this session running."""

        self._require_session_id(session_id)
        await self._connect_with_retry()
        async with self._send_lock:
            await self._send_control("start", session_id)
            self._session_id = session_id
            self._accepting_audio = True
        logger.info("transport session started")

    async def send_audio(self, chunk: bytes) -> None:
        """Send one PCM chunk, or drop it when the stream is not running."""

        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        async with self._send_lock:
            if self._failed or not self._accepting_audio or self._socket is None:
                return
            if not chunk:
                return
            try:
                await self._socket.send(chunk)
            except Exception as exc:
                self._fail_closed()
                logger.warning("transport audio send failed closed")
                raise TransportError("audio send failed") from exc

    async def pause_session(self) -> None:
        """Stop audio on the wire, then tell the server."""

        async with self._send_lock:
            self._accepting_audio = False
            await self._send_control("pause", self._required_id())
        logger.info("transport session paused")

    async def resume_session(self) -> None:
        """Tell the server to resume, then allow audio again."""

        async with self._send_lock:
            if self._failed:
                raise TransportError("transport is closed")
            await self._send_control("resume", self._required_id())
            self._accepting_audio = True
        logger.info("transport session resumed")

    async def stop_session(self) -> None:
        """Tell the server to stop, then close. Further audio is dropped."""

        async with self._send_lock:
            self._accepting_audio = False
            if self._socket is not None and not self._failed and self._session_id is not None:
                try:
                    await self._send_control("stop", self._session_id)
                except TransportError:
                    logger.warning("transport stop failed closed")
            await self._close_socket()
            self._session_id = None
        logger.info("transport session stopped")

    async def _connect_with_retry(self) -> None:
        if self._failed:
            raise TransportError("transport is closed")
        if self._socket is not None:
            return
        last_error: Exception | None = None
        for attempt in range(1, self._handshake_attempts + 1):
            try:
                self._socket = await self._connector.connect()
                logger.info("transport handshake connected")
                return
            except Exception as exc:
                last_error = exc
                logger.warning("transport handshake failed attempt=%s", attempt)
        self._fail_closed()
        raise TransportError("transport handshake failed") from last_error

    async def _send_control(self, action: str, session_id: str) -> None:
        if action not in _ACTIONS:
            raise TransportError("unknown session action")
        socket = self._socket
        if socket is None or self._failed:
            raise TransportError("transport is closed")
        payload = json.dumps(
            {"action": action, "session_id": session_id},
            separators=(",", ":"),
        )
        try:
            await socket.send(payload)
            reply = await socket.recv()
        except TransportError:
            raise
        except Exception as exc:
            self._fail_closed()
            raise TransportError("control send failed") from exc
        if not isinstance(reply, str):
            self._fail_closed()
            raise TransportError("control reply was not text")
        try:
            body = json.loads(reply)
        except json.JSONDecodeError as exc:
            self._fail_closed()
            raise TransportError("control reply was not json") from exc
        if body.get("ok") is not True:
            self._fail_closed()
            raise TransportError("control rejected")

    def _fail_closed(self) -> None:
        self._failed = True
        self._accepting_audio = False

    async def _close_socket(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is None:
            return
        try:
            await socket.close()
        except Exception:
            logger.warning("transport socket close failed")

    def _required_id(self) -> str:
        if self._session_id is None:
            raise TransportError("session has not started")
        return self._session_id

    @staticmethod
    def _require_session_id(session_id: str) -> None:
        if not session_id or len(session_id) > 128:
            raise ValueError("session_id must be 1 to 128 characters")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if any(character not in allowed for character in session_id):
            raise ValueError("session_id has characters that are not allowed")
