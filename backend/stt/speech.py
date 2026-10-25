"""Continuous speech recognition into final utterance text.

Accepts canonical PCM while a session is running and emits final
utterances. In-progress (partial) text goes only to an optional partial
listener, used for the window's live question display; partials never
enter the transcript or the stream of finals. Pause stops recognition without clearing the
caller's transcript. Audio stays in memory; this module does not write files
or log samples or recognized text. The Azure SDK is imported only when the
production adapter is constructed without an injected SDK. See
docs/session-model.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000
_SAMPLE_WIDTH_BITS = 16
_CHANNELS = 1


class SpeechRefused(Exception):
    """Audio was pushed while recognition was not running."""


class SpeechAdapter(Protocol):
    """Vendor recognizer. Implementations must not persist audio."""

    async def start(
        self,
        on_final: Callable[[str], None],
        on_error: Callable[[], None],
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        """Begin continuous recognition. `on_partial` gets in-progress text, if given."""

    async def push(self, chunk: bytes) -> None:
        """Push one PCM chunk."""

    async def pause(self) -> None:
        """Stop recognition. Do not discard the caller's transcript."""

    async def resume(self) -> None:
        """Continue recognition."""

    async def stop(self) -> None:
        """End recognition and release the audio stream."""


class TranscriptStream:
    """Final utterance strings for one recognition session."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()

    def push(self, text: str) -> None:
        if text.strip():
            self._queue.put_nowait(text)

    def close(self) -> None:
        self._queue.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[str]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


class SpeechToText:
    """Continuous recognition. Finals go to the stream; partials only to a partial listener."""

    def __init__(self, adapter: SpeechAdapter) -> None:
        self._adapter = adapter
        self._running = False
        self._failed = False
        self._stopped = False
        self._stream = TranscriptStream()
        self._listener: Callable[[str], None] | None = None
        self._partial_listener: Callable[[str], None] | None = None

    def set_partial_listener(self, listener: Callable[[str], None]) -> None:
        """Receive in-progress text. Not transcript; the listener must not log it."""

        self._partial_listener = listener

    def set_listener(self, listener: Callable[[str], None]) -> None:
        """Receive each final utterance. The listener must not log the text."""

        self._listener = listener

    async def start_session(self) -> TranscriptStream:
        """Start recognition and return the stream of final utterances."""

        if self._running:
            raise RuntimeError("recognition is already started")
        self._failed = False
        await self._adapter.start(self._handle_final, self._handle_error, on_partial=self._handle_partial)
        self._running = True
        logger.info("recognition started")
        return self._stream

    async def push_audio(self, chunk: bytes) -> None:
        """Push PCM. Refused when paused, stopped, or after an error."""

        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        if len(chunk) % 2 != 0:
            raise ValueError("PCM chunk is not aligned to int16 samples")
        if self._failed or not self._running:
            raise SpeechRefused("recognition is not accepting audio")
        if not chunk:
            return
        await self._adapter.push(chunk)

    async def pause(self) -> None:
        """Stop recognition. Already emitted finals stay with the caller."""

        if not self._running:
            return
        self._running = False
        await self._adapter.pause()
        logger.info("recognition paused")

    async def resume(self) -> None:
        """Continue recognition after pause."""

        if self._failed:
            raise SpeechRefused("recognition failed closed")
        if self._running:
            return
        await self._adapter.resume()
        self._running = True
        logger.info("recognition resumed")

    async def stop(self) -> None:
        """End recognition."""

        if self._stopped:
            return
        self._stopped = True
        self._running = False
        await self._adapter.stop()
        self._stream.close()
        logger.info("recognition stopped")

    def _handle_final(self, text: str) -> None:
        if self._failed or not self._running:
            return
        if not text.strip():
            return
        self._stream.push(text)
        if self._listener is not None:
            self._listener(text)

    def _handle_partial(self, text: str) -> None:
        if self._failed or not self._running or not text.strip():
            return
        if self._partial_listener is not None:
            self._partial_listener(text)

    def _handle_error(self) -> None:
        self._failed = True
        self._running = False
        logger.warning("recognition failed closed")


class AzureSpeechAdapter:
    """Azure continuous recognition over an in-memory push stream."""

    def __init__(
        self,
        key: str,
        region: str,
        language: str = "en-US",
        endpoint: str | None = None,
        sdk: Any = None,
    ) -> None:
        if not key or not region:
            raise ValueError("speech key and region are required")
        self._key = key
        self._region = region
        self._language = language or "en-US"
        self._endpoint = endpoint or None
        self._sdk = sdk
        self._push: Any = None
        self._recognizer: Any = None
        self._on_final: Callable[[str], None] | None = None
        self._on_error: Callable[[], None] | None = None
        self._on_partial: Callable[[str], None] | None = None

    async def start(
        self,
        on_final: Callable[[str], None],
        on_error: Callable[[], None],
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        self._on_final = on_final
        self._on_error = on_error
        self._on_partial = on_partial
        sdk = self._sdk_module()
        if self._endpoint:
            config = sdk.SpeechConfig(subscription=self._key, endpoint=self._endpoint)
        else:
            config = sdk.SpeechConfig(subscription=self._key, region=self._region)
        config.speech_recognition_language = self._language
        stream_format = sdk.audio.AudioStreamFormat(
            samples_per_second=_SAMPLE_RATE,
            bits_per_sample=_SAMPLE_WIDTH_BITS,
            channels=_CHANNELS,
        )
        self._push = sdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = sdk.audio.AudioConfig(stream=self._push)
        self._recognizer = sdk.SpeechRecognizer(
            speech_config=config,
            audio_config=audio_config,
        )
        self._recognizer.recognized.connect(self._recognized)
        if on_partial is not None:
            self._recognizer.recognizing.connect(self._recognizing)
        self._recognizer.canceled.connect(self._canceled)
        await asyncio.to_thread(self._recognizer.start_continuous_recognition)

    async def push(self, chunk: bytes) -> None:
        if self._push is None:
            raise SpeechRefused("recognition stream is not open")
        await asyncio.to_thread(self._push.write, chunk)

    async def pause(self) -> None:
        if self._recognizer is None:
            return
        await asyncio.to_thread(self._recognizer.stop_continuous_recognition)

    async def resume(self) -> None:
        if self._recognizer is None:
            raise SpeechRefused("recognition stream is not open")
        await asyncio.to_thread(self._recognizer.start_continuous_recognition)

    async def stop(self) -> None:
        if self._recognizer is not None:
            await asyncio.to_thread(self._recognizer.stop_continuous_recognition)
        if self._push is not None:
            await asyncio.to_thread(self._push.close)
            self._push = None

    def _sdk_module(self) -> Any:
        if self._sdk is None:
            import azure.cognitiveservices.speech as speechsdk

            self._sdk = speechsdk
        return self._sdk

    def _recognized(self, event: Any) -> None:
        sdk = self._sdk_module()
        result = getattr(event, "result", None)
        if result is None:
            return
        if result.reason != sdk.ResultReason.RecognizedSpeech:
            return
        text = getattr(result, "text", "")
        if text and self._on_final is not None:
            self._on_final(text)

    def _recognizing(self, event: Any) -> None:
        sdk = self._sdk_module()
        result = getattr(event, "result", None)
        if result is None or result.reason != sdk.ResultReason.RecognizingSpeech:
            return
        text = getattr(result, "text", "")
        if text and self._on_partial is not None:
            self._on_partial(text)

    def _canceled(self, event: Any) -> None:
        sdk = self._sdk_module()
        details = getattr(event, "cancellation_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason is None:
            reason = getattr(event, "reason", None)
        if reason != sdk.CancellationReason.Error:
            return
        if self._on_error is not None:
            self._on_error()
