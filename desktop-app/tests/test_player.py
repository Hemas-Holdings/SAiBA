"""Segment E2: local playback consumes the whole stream and drives the mute flag."""

from __future__ import annotations

import asyncio

import pytest

from playback.player import AudioPlayer


class MockSink:
    def __init__(self, fail: bool = False) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self._fail = fail

    def write(self, chunk: bytes) -> None:
        if self._fail:
            raise OSError("device unplugged")
        self.writes.append(chunk)

    def close(self) -> None:
        self.closed = True


async def _stream(chunks):
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


async def test_every_chunk_is_played_without_drops() -> None:
    chunks = [bytes([i, 0]) * 256 for i in range(20)]
    sink = MockSink()
    played = await AudioPlayer(sink, tail_seconds=0).play(_stream(chunks))
    assert b"".join(sink.writes) == b"".join(chunks)
    assert played == sum(len(c) for c in chunks)


async def test_odd_byte_chunks_are_carried_to_keep_whole_samples() -> None:
    sink = MockSink()
    await AudioPlayer(sink, tail_seconds=0).play(_stream([b"\x01", b"\x02\x03", b"\x04"]))
    assert all(len(w) % 2 == 0 for w in sink.writes)
    assert b"".join(sink.writes) == b"\x01\x02\x03\x04"


async def test_speaking_covers_the_answer_and_its_tail() -> None:
    gate = asyncio.Event()
    player = AudioPlayer(MockSink(), tail_seconds=0.05)

    async def stream():
        yield b"\x00\x00"
        await gate.wait()
        yield b"\x00\x00"

    assert not player.speaking
    task = asyncio.create_task(player.play(stream()))
    await asyncio.sleep(0.01)
    assert player.speaking
    gate.set()
    await asyncio.sleep(0.02)
    assert player.speaking, "tail should keep capture muted briefly"
    await task
    assert not player.speaking


async def test_device_failure_still_unmutes_capture() -> None:
    player = AudioPlayer(MockSink(fail=True), tail_seconds=0)
    with pytest.raises(OSError):
        await player.play(_stream([b"\x00\x00"]))
    assert not player.speaking


async def test_answers_play_one_at_a_time() -> None:
    sink = MockSink()
    player = AudioPlayer(sink, tail_seconds=0)
    await asyncio.gather(
        player.play(_stream([b"\x01\x00", b"\x01\x00"])),
        player.play(_stream([b"\x02\x00", b"\x02\x00"])),
    )
    assert sink.writes in (
        [b"\x01\x00", b"\x01\x00", b"\x02\x00", b"\x02\x00"],
        [b"\x02\x00", b"\x02\x00", b"\x01\x00", b"\x01\x00"],
    )


def test_close_releases_the_sink_and_tail_must_be_non_negative() -> None:
    sink = MockSink()
    AudioPlayer(sink).close()
    assert sink.closed
    with pytest.raises(ValueError):
        AudioPlayer(sink, tail_seconds=-1)
