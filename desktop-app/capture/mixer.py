"""In-memory mixer for loopback and microphone PCM streams.

Takes two canonical PCM async iterators and yields a saturating sum. Uneven
chunk sizes are buffered until both sides can contribute, then aligned.
If one stream ends first, the remainder is mixed with silence.

Mixed bytes are yielded to the caller (listen-through now; session
streaming in later sprints). This module never writes audio to disk and
never logs samples.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from capture.audio_format import SAMPLE_WIDTH, mix_pcm16_mono, silence

logger = logging.getLogger(__name__)


class AudioMixer:
    """Segment A3: combine two canonical PCM streams."""

    async def mix(
        self,
        stream_a: AsyncIterator[bytes],
        stream_b: AsyncIterator[bytes],
    ) -> AsyncIterator[bytes]:
        """Yield mixed PCM until both input streams are exhausted."""

        logger.info("mixer started")
        buf_a = bytearray()
        buf_b = bytearray()
        a_done = False
        b_done = False
        try:
            while True:
                if not a_done and len(buf_a) < SAMPLE_WIDTH:
                    a_done = not await self._pull(stream_a, buf_a)
                if not b_done and len(buf_b) < SAMPLE_WIDTH:
                    b_done = not await self._pull(stream_b, buf_b)

                if len(buf_a) >= SAMPLE_WIDTH and len(buf_b) >= SAMPLE_WIDTH:
                    nbytes = min(len(buf_a), len(buf_b))
                    nbytes -= nbytes % SAMPLE_WIDTH
                    yield mix_pcm16_mono(bytes(buf_a[:nbytes]), bytes(buf_b[:nbytes]))
                    del buf_a[:nbytes]
                    del buf_b[:nbytes]
                    continue

                if a_done and len(buf_b) >= SAMPLE_WIDTH:
                    nbytes = len(buf_b) - (len(buf_b) % SAMPLE_WIDTH)
                    yield mix_pcm16_mono(silence(nbytes // SAMPLE_WIDTH), bytes(buf_b[:nbytes]))
                    del buf_b[:nbytes]
                    continue

                if b_done and len(buf_a) >= SAMPLE_WIDTH:
                    nbytes = len(buf_a) - (len(buf_a) % SAMPLE_WIDTH)
                    yield mix_pcm16_mono(bytes(buf_a[:nbytes]), silence(nbytes // SAMPLE_WIDTH))
                    del buf_a[:nbytes]
                    continue

                if a_done and b_done:
                    break
        finally:
            logger.info("mixer stopped")

    async def _pull(self, stream: AsyncIterator[bytes], buf: bytearray) -> bool:
        """Append the next chunk. Return False when the stream is exhausted."""

        try:
            chunk = await anext(stream)
        except StopAsyncIteration:
            return False
        if not chunk:
            return False
        if len(chunk) % SAMPLE_WIDTH != 0:
            raise ValueError("PCM chunk is not aligned to int16 samples")
        buf.extend(chunk)
        return True
