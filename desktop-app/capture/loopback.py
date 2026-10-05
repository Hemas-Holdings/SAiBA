"""WASAPI loopback capture (remote / speaker audio).

Uses `pyaudiowpatch` on Windows to open the default render device's loopback
endpoint. PCM is converted to the canonical in-memory format and yielded to
the mixer. This module never writes audio to disk.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from capture.audio_format import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH
from capture.backend import CaptureBackend, PcmChunkCapture

logger = logging.getLogger(__name__)


class PyAudioWPatchBackend:
    """Blocking WASAPI loopback device. Windows-only."""

    def __init__(self) -> None:
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.sample_width = SAMPLE_WIDTH
        self._pyaudio: Any = None
        self._stream: Any = None

    def open(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "WASAPI loopback capture requires Windows and pyaudiowpatch. "
                "Unit tests must inject a mock backend."
            )
        import pyaudiowpatch as pyaudio

        self._pyaudio = pyaudio.PyAudio()
        device = self._find_loopback_device(pyaudio)
        self.sample_rate = int(device["defaultSampleRate"])
        self.channels = int(device["maxInputChannels"]) or 1
        logger.info("loopback device opened")
        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=int(device["index"]),
            frames_per_buffer=self._frames_per_buffer(),
        )

    def read(self, frame_count: int) -> bytes:
        if self._stream is None:
            raise RuntimeError("loopback backend is not open")
        return self._stream.read(frame_count, exception_on_overflow=False)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                logger.warning("loopback stream stop failed")
            try:
                self._stream.close()
            except Exception:
                logger.warning("loopback stream close failed")
            self._stream = None
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                logger.warning("loopback PyAudio terminate failed")
            self._pyaudio = None

    def _frames_per_buffer(self) -> int:
        from capture.audio_format import FRAMES_PER_CHUNK

        if self.sample_rate == SAMPLE_RATE:
            return FRAMES_PER_CHUNK
        return max(1, int(round(FRAMES_PER_CHUNK * self.sample_rate / SAMPLE_RATE)))

    def _find_loopback_device(self, pyaudio: Any) -> dict[str, Any]:
        if self._pyaudio is None:
            raise RuntimeError("loopback backend is not open")
        wasapi_info = self._pyaudio.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = self._pyaudio.get_device_info_by_index(
            int(wasapi_info["defaultOutputDevice"])
        )
        if default_speakers.get("isLoopbackDevice"):
            return default_speakers
        speaker_name = str(default_speakers.get("name", ""))
        for loopback in self._pyaudio.get_loopback_device_info_generator():
            if speaker_name and speaker_name in str(loopback.get("name", "")):
                return loopback
        raise RuntimeError("no WASAPI loopback device found for the default output")


class LoopbackCapture(PcmChunkCapture):
    """Segment A1: WASAPI loopback capture wrapper."""

    def __init__(self, backend: CaptureBackend | None = None) -> None:
        super().__init__(backend if backend is not None else PyAudioWPatchBackend())
