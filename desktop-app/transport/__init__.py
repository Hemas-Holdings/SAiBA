"""Secure desktop-to-backend streaming client.

Sends session control (start/pause/resume/stop) and mixed PCM only while
the session is running, over TLS with a scoped token or mutual TLS. Pause
cuts the stream. A send failure drops unsent audio instead of flushing it.
Audio bytes and tokens are not logged or written to disk.
"""

from transport.client import (
    BackendClient,
    MutualTlsAuth,
    TokenAuth,
    TransportError,
    WebSocketConnector,
)
from transport.streamer import SessionStreamer

__all__ = [
    "BackendClient",
    "MutualTlsAuth",
    "SessionStreamer",
    "TokenAuth",
    "TransportError",
    "WebSocketConnector",
]
