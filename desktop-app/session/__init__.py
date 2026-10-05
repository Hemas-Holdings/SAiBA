"""Local session control for the desktop capture app.

Owns Start / Pause / Resume / Stop and the in-memory jitter buffer. Mixed
PCM stays in memory and is yielded only while the session is running.
Pause and Stop discard buffered audio. This package never writes audio to
disk, never logs samples, and has no network entry point. See
docs/session-model.md.
"""

from session.buffer import BufferRefused, StreamBuffer
from session.controller import InvalidSessionTransition, SessionController, SessionState

__all__ = [
    "BufferRefused",
    "InvalidSessionTransition",
    "SessionController",
    "SessionState",
    "StreamBuffer",
]
