"""WASAPI loopback capture (remote / speaker audio).

Uses `pyaudiowpatch` on Windows to open the default render device's loopback
endpoint. PCM is converted to the canonical in-memory format and yielded to
the mixer. This module never writes audio to disk.

WASAPI loopback delivers no data at all while nothing is playing through
the speakers, so a blocking read would wait forever (and stall the
microphone behind it in the mixer). Reads therefore never block: when the
speakers stay silent for one chunk's duration, a chunk of silence stands in
for them. Read and close share a lock, so Pause or Stop never close the
device while a read is inside it.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from capture.audio_format import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH
from capture.backend import CaptureBackend, PcmChunkCapture

logger = logging.getLogger(__name__)


class PyAudioWPatchBackend:
    """Blocking WASAPI loopback device. Windows-only."""

    _POLL_SECONDS = 0.005
    _RESYNC_SECONDS = 1.0

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.sample_width = SAMPLE_WIDTH
        self._pyaudio: Any = None
        self._stream: Any = None
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._schedule: float | None = None

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
        """Return `frame_count` device frames, waiting at most one chunk's duration.

        If the speakers have produced that many frames, return them. If the
        chunk's duration passes first, return whatever arrived padded with
        silence, so capture keeps real time while nothing is playing.

        Silent chunks follow a running schedule (each deadline is the last
        one plus one chunk), so polling overshoot does not accumulate and
        loopback keeps pace with the microphone it is mixed with.
        """

        now = self._clock()
        if self._schedule is None or now - self._schedule > self._RESYNC_SECONDS:
            # First read, or capture was paused or stalled: start from now.
            self._schedule = now
        deadline = self._schedule + frame_count / self.sample_rate
        while True:
            with self._lock:
                stream = self._require_stream()
                available = stream.get_read_available()
                if available >= frame_count:
                    data = stream.read(frame_count, exception_on_overflow=False)
                    # The device sets the pace while audio is playing.
                    self._schedule = self._clock()
                    return data
                if self._clock() >= deadline:
                    self._schedule = deadline
                    return self._read_padded(stream, available, frame_count)
            self._sleep(self._POLL_SECONDS)

    def _read_padded(self, stream: Any, available: int, frame_count: int) -> bytes:
        frame_bytes = self.sample_width * self.channels
        data = b""
        if available > 0:
            data = stream.read(available, exception_on_overflow=False)
        missing = frame_count * frame_bytes - len(data)
        # bytes(n) is n zero bytes: digital silence for the frames that did not arrive.
        return data + bytes(missing)

    def _require_stream(self) -> Any:
        if self._stream is None:
            raise RuntimeError("loopback backend is not open")
        return self._stream

    def close(self) -> None:
        # Taken with the read lock: the device is never closed mid-read.
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        self._schedule = None
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
