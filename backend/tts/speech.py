"""Streaming text-to-speech for assistant answers.

Input is answer text that is already on the session transcript. Output is
canonical PCM (16 kHz mono int16) as it is synthesized, for the playback
socket. Azure never plays audio on the server. Neither the text nor the
audio is written to disk or logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-JennyNeural"
_DONE = object()


class SynthesisFailed(Exception):
    """Synthesis did not complete. Audio already yielded may be partial."""


class SynthesisAdapter(Protocol):
    def speak(self, text: str, on_audio: Callable[[bytes], None]) -> None:
        """Blocking: synthesize `text`, calling `on_audio` per chunk. Raises on failure."""


class TextToSpeech:
    """Async streaming wrapper over a blocking synthesis adapter."""

    def __init__(self, adapter: SynthesisAdapter) -> None:
        self._adapter = adapter
        self._running: set[asyncio.Task[None]] = set()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM chunks for `text` as they are produced."""

        if not text.strip():
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()

        def on_audio(chunk: bytes) -> None:
            if chunk:
                loop.call_soon_threadsafe(queue.put_nowait, bytes(chunk))

        async def run() -> None:
            try:
                await asyncio.to_thread(self._adapter.speak, text, on_audio)
            except Exception as error:  # noqa: BLE001 - surfaced to the consumer
                loop.call_soon_threadsafe(queue.put_nowait, error)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        task = asyncio.create_task(run(), name="tts-synthesize")
        # Held until done: a consumer that stops early leaves the blocking
        # call to finish in its thread, and its output is discarded.
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                logger.warning("synthesis failed error=%s", type(item).__name__)
                if isinstance(item, SynthesisFailed):
                    raise item
                raise SynthesisFailed("synthesis failed") from item
            yield item  # type: ignore[misc]
        logger.info("synthesis completed")


class AzureSpeechSynthesisAdapter:
    """Azure Speech synthesis to raw PCM in memory. No speaker output, no files."""

    def __init__(
        self,
        key: str,
        region: str,
        voice: str = DEFAULT_VOICE,
        endpoint: str | None = None,
        sdk: Any = None,
    ) -> None:
        if not key or not (region or endpoint):
            raise ValueError("speech key and region are required")
        self._key = key
        self._region = region
        self._voice = voice or DEFAULT_VOICE
        self._endpoint = endpoint or None
        self._sdk = sdk

    def speak(self, text: str, on_audio: Callable[[bytes], None]) -> None:
        sdk = self._sdk_module()
        if self._endpoint:
            config = sdk.SpeechConfig(subscription=self._key, endpoint=self._endpoint)
        else:
            config = sdk.SpeechConfig(subscription=self._key, region=self._region)
        config.speech_synthesis_voice_name = self._voice
        config.set_speech_synthesis_output_format(
            sdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
        )
        # audio_config=None keeps audio in memory; the default is the server speaker.
        synthesizer = sdk.SpeechSynthesizer(speech_config=config, audio_config=None)

        def synthesizing(event: Any) -> None:
            result = getattr(event, "result", None)
            data = getattr(result, "audio_data", None) if result is not None else None
            if data:
                on_audio(bytes(data))

        synthesizer.synthesizing.connect(synthesizing)
        result = synthesizer.speak_text(text)
        if result.reason != sdk.ResultReason.SynthesizingAudioCompleted:
            raise SynthesisFailed("synthesis was canceled")

    def _sdk_module(self) -> Any:
        if self._sdk is None:
            import azure.cognitiveservices.speech as speechsdk

            self._sdk = speechsdk
        return self._sdk
