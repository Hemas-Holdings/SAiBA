"""Segment E1: streaming TTS wrapper and the Azure synthesis adapter (faked SDK)."""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from tts.speech import AzureSpeechSynthesisAdapter, SynthesisFailed, TextToSpeech


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _Event:
    def __init__(self, data: bytes) -> None:
        self.result = type("R", (), {"audio_data": data})()


class FakeSynthSDK:
    """Mimics the parts of azure.cognitiveservices.speech used for synthesis."""

    class ResultReason:
        SynthesizingAudioCompleted = "completed"
        Canceled = "canceled"

    class SpeechSynthesisOutputFormat:
        Raw16Khz16BitMonoPcm = "raw-16khz-16bit-mono-pcm"

    def __init__(self, chunks: list[bytes], reason: str = "completed") -> None:
        self._chunks = chunks
        self._reason = reason
        self.configs: list[FakeSynthSDK._Config] = []
        self.synthesizers: list[dict[str, object]] = []
        self.spoken: list[str] = []
        self.threads: list[str] = []

    class _Config:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.speech_synthesis_voice_name = None
            self.output_format = None

        def set_speech_synthesis_output_format(self, value: object) -> None:
            self.output_format = value

    def SpeechConfig(self, **kwargs: object) -> "FakeSynthSDK._Config":
        config = FakeSynthSDK._Config(**kwargs)
        self.configs.append(config)
        return config

    def SpeechSynthesizer(self, speech_config: object, audio_config: object = "speaker") -> object:
        sdk = self
        record: dict[str, object] = {"audio_config": audio_config}
        self.synthesizers.append(record)

        class _Synth:
            synthesizing = _Signal()

            def speak_text(self, text: str) -> object:
                sdk.spoken.append(text)
                sdk.threads.append(threading.current_thread().name)
                for chunk in sdk._chunks:
                    for callback in self.synthesizing.callbacks:
                        callback(_Event(chunk))
                return type("Result", (), {"reason": sdk._reason})()

        return _Synth()


async def _collect(stream) -> list[bytes]:
    return [chunk async for chunk in stream]


async def test_azure_adapter_requests_raw_pcm_with_voice_and_no_speaker() -> None:
    sdk = FakeSynthSDK([b"\x01\x02", b"", b"\x03\x04"])
    adapter = AzureSpeechSynthesisAdapter("key", "eastus", voice="en-GB-SoniaNeural", sdk=sdk)
    chunks = await _collect(TextToSpeech(adapter).synthesize("Revenue was steady."))
    assert chunks == [b"\x01\x02", b"\x03\x04"]
    config = sdk.configs[0]
    assert config.kwargs == {"subscription": "key", "region": "eastus"}
    assert config.speech_synthesis_voice_name == "en-GB-SoniaNeural"
    assert config.output_format == "raw-16khz-16bit-mono-pcm"
    assert sdk.synthesizers[0]["audio_config"] is None
    assert sdk.spoken == ["Revenue was steady."]
    assert sdk.threads[0] != threading.main_thread().name


async def test_endpoint_is_used_instead_of_region() -> None:
    sdk = FakeSynthSDK([b"\x00\x00"])
    adapter = AzureSpeechSynthesisAdapter("key", "", endpoint="https://example", sdk=sdk)
    await _collect(TextToSpeech(adapter).synthesize("hi"))
    assert sdk.configs[0].kwargs == {"subscription": "key", "endpoint": "https://example"}


async def test_canceled_synthesis_raises_after_partial_audio(caplog) -> None:
    sdk = FakeSynthSDK([b"\x01\x02"], reason="canceled")
    tts = TextToSpeech(AzureSpeechSynthesisAdapter("key", "eastus", sdk=sdk))
    received: list[bytes] = []
    with caplog.at_level(logging.DEBUG), pytest.raises(SynthesisFailed):
        async for chunk in tts.synthesize("secret answer text"):
            received.append(chunk)
    assert received == [b"\x01\x02"]
    assert "secret answer text" not in caplog.text


async def test_unexpected_adapter_error_becomes_synthesis_failed() -> None:
    class Broken:
        def speak(self, text, on_audio) -> None:
            raise OSError("network")

    with pytest.raises(SynthesisFailed):
        await _collect(TextToSpeech(Broken()).synthesize("hello"))


async def test_blank_text_yields_nothing_and_does_not_call_azure() -> None:
    sdk = FakeSynthSDK([b"\x01\x02"])
    assert await _collect(TextToSpeech(AzureSpeechSynthesisAdapter("k", "r", sdk=sdk)).synthesize("  ")) == []
    assert sdk.spoken == []


async def test_consumer_stopping_early_does_not_break_later_calls() -> None:
    sdk = FakeSynthSDK([b"\x01\x02", b"\x03\x04"])
    tts = TextToSpeech(AzureSpeechSynthesisAdapter("k", "r", sdk=sdk))
    stream = tts.synthesize("one")
    assert await stream.__anext__() == b"\x01\x02"
    await stream.aclose()
    await asyncio.sleep(0.05)
    assert await _collect(tts.synthesize("two")) == [b"\x01\x02", b"\x03\x04"]


def test_credentials_are_required() -> None:
    with pytest.raises(ValueError):
        AzureSpeechSynthesisAdapter("", "eastus")
    with pytest.raises(ValueError):
        AzureSpeechSynthesisAdapter("key", "")
