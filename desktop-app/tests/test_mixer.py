"""Segment A3 unit tests: AudioMixer with synthetic tones, no real audio."""

from __future__ import annotations

import numpy as np
import pytest

from capture.mixer import AudioMixer
from tests.helpers import chunk_stream, sine_pcm


@pytest.mark.asyncio
async def test_mixer_preserves_both_tone_peaks() -> None:
    frame_count = 16_000
    stream_a_pcm = sine_pcm(440.0, frame_count)
    stream_b_pcm = sine_pcm(880.0, frame_count)
    chunk = 1024
    chunks_a = [stream_a_pcm[i : i + chunk] for i in range(0, len(stream_a_pcm), chunk)]
    chunks_b = [stream_b_pcm[i : i + chunk] for i in range(0, len(stream_b_pcm), chunk)]

    mixer = AudioMixer()
    mixed = b"".join(
        [item async for item in mixer.mix(chunk_stream(*chunks_a), chunk_stream(*chunks_b))]
    )
    samples = np.frombuffer(mixed, dtype=np.int16).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(samples.size, 1.0 / 16_000)

    peak_a = int(np.argmin(np.abs(freqs - 440.0)))
    peak_b = int(np.argmin(np.abs(freqs - 880.0)))
    noise_bin = int(np.argmin(np.abs(freqs - 3000.0)))
    assert spectrum[peak_a] > spectrum[noise_bin] * 8
    assert spectrum[peak_b] > spectrum[noise_bin] * 8


@pytest.mark.asyncio
async def test_mixer_does_not_corrupt_signal_when_other_is_silence() -> None:
    tone = sine_pcm(440.0, 2048, amplitude=1000)
    quiet = b"\x00" * len(tone)
    mixer = AudioMixer()
    mixed = b"".join(
        [item async for item in mixer.mix(chunk_stream(tone), chunk_stream(quiet))]
    )
    original = np.frombuffer(tone, dtype=np.int16)
    result = np.frombuffer(mixed, dtype=np.int16)
    np.testing.assert_array_equal(original, result)


@pytest.mark.asyncio
async def test_mixer_pads_shorter_stream_with_silence() -> None:
    left = b"\x10\x00\x10\x00\x10\x00\x10\x00"
    right = b"\x05\x00\x05\x00"
    mixer = AudioMixer()
    mixed = b"".join(
        [item async for item in mixer.mix(chunk_stream(left), chunk_stream(right))]
    )
    samples = np.frombuffer(mixed, dtype=np.int16)
    assert list(samples) == [21, 21, 16, 16]


@pytest.mark.asyncio
async def test_mixer_pads_when_first_stream_ends_first() -> None:
    left = b"\x05\x00\x05\x00"
    right = b"\x10\x00\x10\x00\x10\x00\x10\x00"
    mixer = AudioMixer()
    mixed = b"".join(
        [item async for item in mixer.mix(chunk_stream(left), chunk_stream(right))]
    )
    samples = np.frombuffer(mixed, dtype=np.int16)
    assert list(samples) == [21, 21, 16, 16]


@pytest.mark.asyncio
async def test_mixer_empty_streams() -> None:
    mixer = AudioMixer()
    mixed = [item async for item in mixer.mix(chunk_stream(), chunk_stream())]
    assert mixed == []


@pytest.mark.asyncio
async def test_mixer_treats_empty_chunk_as_end() -> None:
    mixer = AudioMixer()
    mixed = [item async for item in mixer.mix(chunk_stream(b""), chunk_stream(b"\x01\x00"))]
    samples = np.frombuffer(b"".join(mixed), dtype=np.int16)
    assert list(samples) == [1]


@pytest.mark.asyncio
async def test_mixer_rejects_unaligned_chunk() -> None:
    mixer = AudioMixer()
    with pytest.raises(ValueError, match="aligned"):
        async for _ in mixer.mix(chunk_stream(b"\x00"), chunk_stream(b"\x00\x00")):
            pass
