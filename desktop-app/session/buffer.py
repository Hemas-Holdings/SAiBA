"""Bounded in-memory jitter buffer for mixed PCM.

Holds a short stretch of canonical PCM so a later stream can absorb jitter.
This is not a recording of the meeting. Pause and Stop clear it, and writes
are refused while it is not accepting. Bytes stay in this process only:
nothing here writes a file or logs sample contents.
"""

from __future__ import annotations

from capture.audio_format import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH

DEFAULT_STREAM_BUFFER_SECONDS = 8.0


class BufferRefused(Exception):
    """Raised when `write` is called while the buffer is not accepting."""


class StreamBuffer:
    """In-memory PCM buffer with a fixed byte cap."""

    def __init__(self, max_seconds: float = DEFAULT_STREAM_BUFFER_SECONDS) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        frames = int(max_seconds * SAMPLE_RATE)
        if frames < 1:
            raise ValueError("max_seconds is below one PCM frame")
        self._capacity = frames * CHANNELS * SAMPLE_WIDTH
        self._data = bytearray()
        self._accepting = False

    @property
    def capacity(self) -> int:
        """Maximum stored size in bytes."""

        return self._capacity

    def __len__(self) -> int:
        return len(self._data)

    def accept(self) -> None:
        """Allow `write` calls. Used when a session is running."""

        self._accepting = True

    def refuse(self) -> None:
        """Reject further `write` calls. Stored bytes stay until `clear`."""

        self._accepting = False

    def write(self, chunk: bytes) -> None:
        """Append PCM, dropping the oldest bytes if the cap would be exceeded."""

        if not self._accepting:
            raise BufferRefused("buffer is not accepting writes")
        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        if len(chunk) % SAMPLE_WIDTH != 0:
            raise ValueError("PCM chunk is not aligned to int16 samples")
        if not chunk:
            return
        self._data.extend(chunk)
        extra = len(self._data) - self._capacity
        if extra > 0:
            del self._data[:extra]

    def read(self) -> bytes:
        """Remove and return whatever is currently stored."""

        if not self._data:
            return b""
        data = bytes(self._data)
        self._data.clear()
        return data

    def clear(self) -> None:
        """Drop every stored byte. Nothing remains readable."""

        self._data.clear()
