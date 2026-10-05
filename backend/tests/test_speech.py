"""Segment D1: continuous recognition with a fake SDK. No Azure calls."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from stt.speech import AzureSpeechAdapter, SpeechRefused, SpeechToText


class Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)


class FakePushStream:
    def __init__(self, stream_format: object | None = None) -> None:
        self.stream_format = stream_format
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def close(self) -> None:
        self.closed = True


class FakeRecognizer:
    def __init__(self) -> None:
        self.recognized = Signal()
        self.canceled = Signal()
        self.started = 0
        self.stopped = 0

    def start_continuous_recognition(self) -> None:
        self.started += 1

    def stop_continuous_recognition(self) -> None:
        self.stopped += 1


class FakeSDK:
    def __init__(self) -> None:
        self.recognizer = FakeRecognizer()
        self.push_stream: FakePushStream | None = None
        self.config: SimpleNamespace | None = None
        self.ResultReason = SimpleNamespace(RecognizedSpeech="recognized", NoMatch="nomatch")
        self.CancellationReason = SimpleNamespace(Error="error", EndOfStream="end")

    def SpeechConfig(
        self,
        subscription: str | None = None,
        region: str | None = None,
        endpoint: str | None = None,
    ) -> SimpleNamespace:
        self.config = SimpleNamespace(
            subscription=subscription,
            region=region,
            endpoint=endpoint,
            speech_recognition_language=None,
        )
        return self.config

    class audio:
        @staticmethod
        def AudioStreamFormat(**kwargs: int) -> dict[str, int]:
            return kwargs

        class PushAudioInputStream:
            owner: FakeSDK

            def __init__(self, stream_format: object | None = None) -> None:
                stream = FakePushStream(stream_format)
                FakeSDK.audio.PushAudioInputStream.owner.push_stream = stream
                self._stream = stream

            def write(self, data: bytes) -> None:
                self._stream.write(data)

            def close(self) -> None:
                self._stream.close()

        class AudioConfig:
            def __init__(self, stream: object | None = None) -> None:
                self.stream = stream

    def SpeechRecognizer(
        self,
        speech_config: object,
        audio_config: object,
    ) -> FakeRecognizer:
        del speech_config, audio_config
        return self.recognizer


def _adapter() -> tuple[AzureSpeechAdapter, FakeSDK]:
    sdk = FakeSDK()
    FakeSDK.audio.PushAudioInputStream.owner = sdk
    return AzureSpeechAdapter("key", "eastus", sdk=sdk), sdk


class MemoryAdapter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0
        self._on_final = None
        self._on_error = None

    async def start(self, on_final, on_error) -> None:
        self._on_final = on_final
        self._on_error = on_error

    async def push(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    async def pause(self) -> None:
        self.pause_calls += 1

    async def resume(self) -> None:
        self.resume_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    def emit(self, text: str) -> None:
        self._on_final(text)

    def fail(self) -> None:
        self._on_error()


@pytest.mark.asyncio
async def test_pause_keeps_prior_finals_and_refuses_audio() -> None:
    adapter = MemoryAdapter()
    speech = SpeechToText(adapter)
    heard: list[str] = []
    speech.set_listener(heard.append)
    await speech.start_session()
    await speech.push_audio(b"\x01\x02")
    adapter.emit("hello room")
    await speech.pause()
    adapter.emit("during pause")
    with pytest.raises(SpeechRefused):
        await speech.push_audio(b"\x03\x04")
    assert heard == ["hello room"]
    assert adapter.pause_calls == 1
    await speech.resume()
    await speech.push_audio(b"\x05\x06")
    adapter.emit("after resume")
    assert heard == ["hello room", "after resume"]
    assert adapter.chunks == [b"\x01\x02", b"\x05\x06"]


@pytest.mark.asyncio
async def test_recognition_error_fails_closed_without_a_guess() -> None:
    adapter = MemoryAdapter()
    speech = SpeechToText(adapter)
    heard: list[str] = []
    speech.set_listener(heard.append)
    await speech.start_session()
    adapter.fail()
    adapter.emit("guessed text")
    with pytest.raises(SpeechRefused):
        await speech.push_audio(b"\x01\x02")
    assert heard == []


@pytest.mark.asyncio
async def test_azure_adapter_forwards_finals_only_and_writes_memory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, sdk = _adapter()
    finals: list[str] = []

    def on_final(text: str) -> None:
        finals.append(text)

    errors: list[str] = []
    with caplog.at_level(logging.DEBUG):
        await adapter.start(on_final, lambda: errors.append("failed"))
        await adapter.push(b"\x11\x22")
        recognized = sdk.recognizer.recognized.callbacks[0]
        recognized(SimpleNamespace(result=SimpleNamespace(reason="nomatch", text="partial secret")))
        recognized(SimpleNamespace(result=SimpleNamespace(reason="recognized", text="hello room")))
        canceled = sdk.recognizer.canceled.callbacks[0]
        canceled(SimpleNamespace(reason="end", cancellation_details=None))
        await adapter.pause()
        await adapter.resume()
        canceled(SimpleNamespace(cancellation_details=SimpleNamespace(reason="error")))
    assert finals == ["hello room"]
    assert errors == ["failed"]
    assert sdk.push_stream is not None
    assert sdk.push_stream.writes == [b"\x11\x22"]
    assert sdk.push_stream.stream_format["samples_per_second"] == 16_000
    assert sdk.recognizer.started == 2
    assert sdk.recognizer.stopped == 1
    assert "hello room" not in caplog.text
    assert "partial secret" not in caplog.text
    assert "\\x11" not in caplog.text
    await adapter.stop()
    assert sdk.push_stream.closed is True


def test_azure_adapter_rejects_empty_credentials() -> None:
    with pytest.raises(ValueError, match="key and region"):
        AzureSpeechAdapter("", "eastus")


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    adapter = MemoryAdapter()
    speech = SpeechToText(adapter)
    await speech.start_session()
    await speech.stop()
    await speech.stop()
    assert adapter.stop_calls == 1
