"""Segment B2: in-memory jitter buffer. No disk, no sample logs."""

from __future__ import annotations

import logging

import pytest

from capture.audio_format import SAMPLE_RATE, SAMPLE_WIDTH
from session.buffer import DEFAULT_STREAM_BUFFER_SECONDS, BufferRefused, StreamBuffer


def test_default_capacity_is_eight_seconds_of_pcm() -> None:
    buffer = StreamBuffer()
    assert buffer.capacity == int(DEFAULT_STREAM_BUFFER_SECONDS * SAMPLE_RATE) * SAMPLE_WIDTH
    assert DEFAULT_STREAM_BUFFER_SECONDS == 8.0


def test_write_refused_until_accepting() -> None:
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    with pytest.raises(BufferRefused, match="not accepting"):
        buffer.write(b"\x00\x00")
    buffer.accept()
    buffer.write(b"\x01\x02")
    assert bytes(buffer.read()) == b"\x01\x02"
    buffer.refuse()
    with pytest.raises(BufferRefused, match="not accepting"):
        buffer.write(b"\x03\x04")
    assert len(buffer) == 0


def test_clear_leaves_nothing_readable() -> None:
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    buffer.accept()
    buffer.write(b"\x01\x02")
    buffer.clear()
    assert len(buffer) == 0
    assert buffer.read() == b""


def test_never_exceeds_capacity_and_keeps_newest_bytes() -> None:
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    assert buffer.capacity == SAMPLE_WIDTH
    buffer.accept()
    buffer.write(b"\x01\x02\x03\x04")
    assert len(buffer) == buffer.capacity
    assert buffer.read() == b"\x03\x04"
    buffer.write(b"\x05\x06")
    buffer.write(b"\x07\x08")
    assert len(buffer) == buffer.capacity
    assert buffer.read() == b"\x07\x08"


def test_empty_chunk_is_ignored_while_accepting() -> None:
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    buffer.accept()
    buffer.write(b"")
    assert len(buffer) == 0


@pytest.mark.parametrize(
    ("chunk", "message"),
    [
        (bytearray(b"\x00\x00"), "must be bytes"),
        (b"\x00", "not aligned"),
    ],
)
def test_write_rejects_bad_chunks(chunk: bytes, message: str) -> None:
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    buffer.accept()
    with pytest.raises((TypeError, ValueError), match=message):
        buffer.write(chunk)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_seconds", [0, -1, 1 / SAMPLE_RATE / 2])
def test_capacity_must_cover_at_least_one_frame(max_seconds: float) -> None:
    with pytest.raises(ValueError):
        StreamBuffer(max_seconds=max_seconds)


def test_buffer_does_not_log_samples_or_open_files(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def reject_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("buffer opened a file")

    monkeypatch.setattr("builtins.open", reject_open)
    monkeypatch.setattr("pathlib.Path.write_bytes", reject_open)
    monkeypatch.setattr("pathlib.Path.write_text", reject_open)
    marker = b"\xab\xcd"
    buffer = StreamBuffer(max_seconds=1 / SAMPLE_RATE)
    with caplog.at_level(logging.DEBUG):
        buffer.accept()
        buffer.write(marker)
        assert buffer.read() == marker
        buffer.write(marker)
        buffer.clear()
    assert "\\xab" not in caplog.text
    assert marker not in [record.args for record in caplog.records]
