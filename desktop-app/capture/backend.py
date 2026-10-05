"""Capture backend protocol and in-memory PCM streaming.

Opens an audio device, reads chunks, converts them to the canonical PCM
format, and yields those bytes to the caller. The ring of data lives only
in memory for the lifetime of the iterator. This module never writes audio
to disk and never logs sample contents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Protocol

from capture.audio_format import FRAMES_PER_CHUNK, SAMPLE_RATE, to_canonical_pcm

logger = logging.getLogger(__name__)


class CaptureBackend(Protocol):
    """Blocking audio device handle used by `PcmChunkCapture`."""

    sample_rate: int
    channels: int
    sample_width: int

    def open(self) -> None:
        """Acquire the device handle."""

    def read(self, frame_count: int) -> bytes:
        """Read `frame_count` native frames. Blocks until they are available."""

    def close(self) -> None:
        """Release the device handle. Must be safe to call more than once."""


class PcmChunkCapture:
    """Yield canonical PCM chunks from a blocking capture backend."""

    def __init__(self, backend: CaptureBackend) -> None:
        self._backend = backend
        self._stop_requested = False
        self._open = False

    async def start(self) -> AsyncIterator[bytes]:
        """Open the device and yield canonical PCM chunks until `stop()`."""

        if self._open:
            raise RuntimeError("capture is already started")
        self._stop_requested = False
        self._backend.open()
        self._open = True
        logger.info("capture started")
        try:
            while not self._stop_requested:
                native_frames = self._native_frame_count()
                try:
                    raw = await asyncio.to_thread(self._backend.read, native_frames)
                except Exception:
                    if self._stop_requested:
                        break
                    raise
                if not raw:
                    break
                yield to_canonical_pcm(
                    raw,
                    sample_rate=self._backend.sample_rate,
                    channels=self._backend.channels,
                )
        finally:
            self._release()
            logger.info("capture stopped")

    def stop(self) -> None:
        """Request the stream to end and release the device handle."""

        self._stop_requested = True
        self._release()

    def _native_frame_count(self) -> int:
        rate = self._backend.sample_rate
        if rate == SAMPLE_RATE:
            return FRAMES_PER_CHUNK
        return max(1, int(round(FRAMES_PER_CHUNK * rate / SAMPLE_RATE)))

    def _release(self) -> None:
        if not self._open:
            return
        self._open = False
        self._backend.close()
