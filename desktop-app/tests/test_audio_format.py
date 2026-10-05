"""Unit tests for canonical PCM conversion. Uses synthetic samples only."""

from __future__ import annotations

import numpy as np
import pytest

from capture.audio_format import (
    SAMPLE_RATE,
    mix_pcm16_mono,
    silence,
    to_canonical_pcm,
)


def test_silence_length() -> None:
    assert silence(4) == b"\x00" * 8


def test_silence_rejects_negative() -> None:
    with pytest.raises(ValueError):
        silence(-1)


def test_identity_when_already_canonical() -> None:
    payload = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    assert to_canonical_pcm(payload, SAMPLE_RATE, 1) == payload


def test_stereo_downmix() -> None:
    # Interleaved int16: left=1000, right=3000 → mono mean=2000
    stereo = np.array([1000, 3000], dtype=np.int16).tobytes()
    mono = np.frombuffer(to_canonical_pcm(stereo, SAMPLE_RATE, 2), dtype=np.int16)
    assert int(mono[0]) == 2000


def test_resample_48000_to_16000_length() -> None:
    frames_48k = 4800
    pcm_48k = np.zeros(frames_48k, dtype=np.int16).tobytes()
    out = to_canonical_pcm(pcm_48k, 48_000, 1)
    assert len(out) == 1600 * 2


def test_rejects_unaligned_bytes() -> None:
    with pytest.raises(ValueError):
        to_canonical_pcm(b"\x00", SAMPLE_RATE, 1)


def test_mix_saturating_sum() -> None:
    a = np.array([20_000, -20_000], dtype=np.int16).tobytes()
    b = np.array([20_000, -20_000], dtype=np.int16).tobytes()
    mixed = np.frombuffer(mix_pcm16_mono(a, b), dtype=np.int16)
    assert int(mixed[0]) == 32767
    assert int(mixed[1]) == -32768


def test_mix_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        mix_pcm16_mono(b"\x00\x00", b"\x00\x00\x00\x00")
