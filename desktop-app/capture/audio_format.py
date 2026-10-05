"""Canonical in-memory PCM format for desktop capture.

This module converts device-native PCM into 16-bit mono 16 kHz bytes.
Audio stays in memory as `bytes`; nothing here writes files or logs samples.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # int16 little-endian
FRAMES_PER_CHUNK = 512  # 32 ms at 16 kHz; bounded in-memory stream chunks
CHUNK_BYTES = FRAMES_PER_CHUNK * CHANNELS * SAMPLE_WIDTH
PCM_DTYPE = np.int16


def silence(frame_count: int = FRAMES_PER_CHUNK) -> bytes:
    """Return `frame_count` frames of int16 silence."""

    if frame_count < 0:
        raise ValueError("frame_count must be >= 0")
    return b"\x00" * (frame_count * SAMPLE_WIDTH * CHANNELS)


def _as_int16_frames(data: bytes, channels: int) -> np.ndarray:
    if channels < 1:
        raise ValueError("channels must be >= 1")
    if len(data) % (SAMPLE_WIDTH * channels) != 0:
        raise ValueError(
            "PCM byte length is not aligned to int16 frames for the given channel count"
        )
    frames = np.frombuffer(data, dtype=PCM_DTYPE)
    if channels == 1:
        return frames.copy()
    return frames.reshape(-1, channels).mean(axis=1).astype(PCM_DTYPE)


def _resample_int16(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate or samples.size == 0:
        return samples
    duration = samples.size / source_rate
    target_length = int(round(duration * target_rate))
    if target_length == 0:
        return np.zeros(0, dtype=PCM_DTYPE)
    source_x = np.linspace(0.0, duration, samples.size, endpoint=False)
    target_x = np.linspace(0.0, duration, target_length, endpoint=False)
    resampled = np.interp(target_x, source_x, samples.astype(np.float64))
    return np.clip(resampled, -32768, 32767).astype(PCM_DTYPE)


def to_canonical_pcm(data: bytes, sample_rate: int, channels: int) -> bytes:
    """Convert device PCM to 16-bit mono 16 kHz.

    `sample_rate` and `channels` describe `data`. Output is always canonical.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    mono = _as_int16_frames(data, channels)
    canonical = _resample_int16(mono, sample_rate, SAMPLE_RATE)
    return canonical.tobytes()


def mix_pcm16_mono(stream_a: bytes, stream_b: bytes) -> bytes:
    """Saturating sum of two equal-length canonical PCM chunks."""

    if len(stream_a) != len(stream_b):
        raise ValueError("mixed PCM chunks must be the same length")
    if len(stream_a) % SAMPLE_WIDTH != 0:
        raise ValueError("PCM byte length is not aligned to int16 samples")
    samples_a = np.frombuffer(stream_a, dtype=PCM_DTYPE).astype(np.int32)
    samples_b = np.frombuffer(stream_b, dtype=PCM_DTYPE).astype(np.int32)
    mixed = np.clip(samples_a + samples_b, -32768, 32767).astype(PCM_DTYPE)
    return mixed.tobytes()
