"""Microphone capture (in-room speech).

Uses `sounddevice` to open the default input device as 16-bit PCM. Chunks
are converted to the canonical in-memory format and yielded to the mixer.
This module never writes audio to disk.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from capture.audio_format import (
    CHANNELS,
    FRAMES_PER_CHUNK,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
)
from capture.backend import CaptureBackend, PcmChunkCapture

logger = logging.getLogger(__name__)


class SoundDeviceMicBackend:
    """Blocking default-microphone device via sounddevice."""

    def __init__(self) -> None:
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.sample_width = SAMPLE_WIDTH
        self._stream: Any = None

    def open(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=FRAMES_PER_CHUNK,
        )
        self._stream.start()
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        logger.info("microphone device opened")

    def read(self, frame_count: int) -> bytes:
        if self._stream is None:
            raise RuntimeError("microphone backend is not open")
        frames, overflowed = self._stream.read(frame_count)
        if overflowed:
            logger.warning("microphone input overflowed")
        return np.ascontiguousarray(frames, dtype=np.int16).tobytes()

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception:
            logger.warning("microphone stream stop failed")
        try:
            self._stream.close()
        except Exception:
            logger.warning("microphone stream close failed")
        self._stream = None


class MicCapture(PcmChunkCapture):
    """Segment A2: microphone capture wrapper."""

    def __init__(self, backend: CaptureBackend | None = None) -> None:
        super().__init__(backend if backend is not None else SoundDeviceMicBackend())
