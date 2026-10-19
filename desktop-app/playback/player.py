"""Play answer audio through the local output device.

Consumes canonical PCM (16 kHz mono int16) for one answer and writes all of
it to an `AudioSink`. While an answer is playing, and for a short tail
after it, `speaking` is true so capture can be muted and the answer is not
transcribed back as meeting speech. Audio is not written to disk or logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from capture.audio_format import SAMPLE_WIDTH
from capture.listen import AudioSink

logger = logging.getLogger(__name__)

DEFAULT_TAIL_SECONDS = 0.3


class AudioPlayer:
    """Plays one answer stream at a time. Not a recorder."""

    def __init__(self, sink: AudioSink, tail_seconds: float = DEFAULT_TAIL_SECONDS) -> None:
        if tail_seconds < 0:
            raise ValueError("tail_seconds must be >= 0")
        self._sink = sink
        self._tail = tail_seconds
        self._lock = asyncio.Lock()
        self._active = 0
        self._speaking = False

    @property
    def speaking(self) -> bool:
        return self._speaking

    async def play(self, stream: AsyncIterator[bytes]) -> int:
        """Write every chunk of `stream` to the sink. Returns bytes played."""

        played = 0
        try:
            async with self._lock:
                self._active += 1
                self._speaking = True
                carry = b""
                try:
                    async for chunk in stream:
                        data = carry + chunk
                        usable = len(data) - (len(data) % SAMPLE_WIDTH)
                        carry = data[usable:]
                        if usable:
                            await asyncio.to_thread(self._sink.write, data[:usable])
                            played += usable
                finally:
                    self._active -= 1
                logger.info("answer playback finished")
            if self._tail:
                await asyncio.sleep(self._tail)
        finally:
            # Always unmute, even if the stream or device failed.
            if self._active == 0 and not self._lock.locked():
                self._speaking = False
        return played

    def close(self) -> None:
        self._sink.close()
