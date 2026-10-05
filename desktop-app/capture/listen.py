"""Live listen-through of the mixed capture stream.

Plays mixed PCM to a caller-supplied sink and then discards it. Used for the
Sprint 1 gate (confirm remote + in-room speech are both audible). This is not
a recorder: there is no path that writes WAV/PCM files.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from capture.audio_format import CHANNELS, SAMPLE_RATE
from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from capture.mixer import AudioMixer

logger = logging.getLogger(__name__)


class AudioSink(Protocol):
    """Destination for mixed PCM. Implementations must not persist audio."""

    def write(self, chunk: bytes) -> None:
        """Accept one canonical PCM chunk."""

    def close(self) -> None:
        """Release the sink."""


class SoundDevicePlaybackSink:
    """Play canonical PCM through the default output device."""

    def __init__(self) -> None:
        import sounddevice as sd
        import numpy as np

        self._np = np
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
        )
        self._stream.start()

    def write(self, chunk: bytes) -> None:
        samples = self._np.frombuffer(chunk, dtype=self._np.int16)
        if samples.size == 0:
            return
        self._stream.write(samples)

    def close(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()


async def run_listen_through(
    duration_seconds: float,
    sink: AudioSink,
    loopback: LoopbackCapture | None = None,
    microphone: MicCapture | None = None,
    mixer: AudioMixer | None = None,
) -> None:
    """Mix loopback + mic for `duration_seconds` and write chunks to `sink`."""

    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")

    loopback_capture = loopback if loopback is not None else LoopbackCapture()
    mic_capture = microphone if microphone is not None else MicCapture()
    audio_mixer = mixer if mixer is not None else AudioMixer()
    deadline = time.monotonic() + duration_seconds
    mixed = audio_mixer.mix(loopback_capture.start(), mic_capture.start())
    logger.info("listen-through started")
    try:
        async for chunk in mixed:
            sink.write(chunk)
            if time.monotonic() >= deadline:
                break
    finally:
        loopback_capture.stop()
        mic_capture.stop()
        sink.close()
        logger.info("listen-through stopped")
