"""Synthetic PCM helpers for unit tests. Never real meeting audio, never files."""

from __future__ import annotations

from capture.audio_format import FRAMES_PER_CHUNK, SAMPLE_RATE, SAMPLE_WIDTH, silence


class MockCaptureBackend:
    """In-memory capture backend that yields a prepared list of PCM chunks."""

    def __init__(
        self,
        chunks: list[bytes] | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = 1,
    ) -> None:
        self.chunks = chunks if chunks is not None else [silence()] * 4
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = SAMPLE_WIDTH
        self.opened = False
        self.close_calls = 0
        self.read_calls = 0
        self._index = 0

    def open(self) -> None:
        if self.opened:
            raise RuntimeError("backend is already open")
        self.opened = True

    def read(self, frame_count: int) -> bytes:
        if not self.opened or self.close_calls:
            raise RuntimeError("backend is not open")
        self.read_calls += 1
        if self._index >= len(self.chunks):
            return silence(frame_count)
        chunk = self.chunks[self._index]
        self._index += 1
        return chunk

    def close(self) -> None:
        self.opened = False
        self.close_calls += 1


def sine_pcm(
    frequency_hz: float,
    frame_count: int,
    sample_rate: int = SAMPLE_RATE,
    amplitude: int = 8000,
) -> bytes:
    """Deterministic int16 sine wave for mixer assertions. Not real speech."""

    import numpy as np

    time_axis = np.arange(frame_count) / sample_rate
    samples = np.sin(2.0 * np.pi * frequency_hz * time_axis) * amplitude
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


async def chunk_stream(*chunks: bytes):
    for chunk in chunks:
        yield chunk


# Re-export for tests that want the production chunk size.
FRAMES_PER_CHUNK_TEST = FRAMES_PER_CHUNK
