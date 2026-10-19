"""Integration test E: real Azure TTS into real local playback. Checked by ear.

Opt-in: `pytest -m integration tests/test_tts_live.py` with AZURE_SPEECH_KEY,
AZURE_SPEECH_REGION (or AZURE_SPEECH_ENDPOINT) and RUN_LIVE_AUDIO=1. Audio is
played and discarded; nothing is written to disk.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tts.speech import AzureSpeechSynthesisAdapter, TextToSpeech

_DESKTOP = Path(__file__).resolve().parents[2] / "desktop-app"
if str(_DESKTOP) not in sys.path:
    sys.path.append(str(_DESKTOP))

_PHRASE = "This is the board assistant. Q3 revenue was twelve point four billion rupees."


@pytest.mark.integration
async def test_azure_tts_is_audible_through_local_playback() -> None:
    key = os.environ.get("AZURE_SPEECH_KEY", "")
    region = os.environ.get("AZURE_SPEECH_REGION", "")
    endpoint = os.environ.get("AZURE_SPEECH_ENDPOINT") or None
    if not key or not (region or endpoint) or os.environ.get("RUN_LIVE_AUDIO") != "1":
        pytest.skip("needs AZURE_SPEECH_KEY, a region or endpoint, and RUN_LIVE_AUDIO=1")

    from capture.listen import SoundDevicePlaybackSink
    from playback.player import AudioPlayer

    tts = TextToSpeech(
        AzureSpeechSynthesisAdapter(
            key,
            region,
            voice=os.environ.get("AZURE_SPEECH_TTS_VOICE") or "en-US-JennyNeural",
            endpoint=endpoint,
        )
    )
    player = AudioPlayer(SoundDevicePlaybackSink())
    try:
        played = await player.play(tts.synthesize(_PHRASE))
    finally:
        player.close()
    # About 5 s of speech at 32 kB/s; listen for the phrase above.
    assert played > 32_000 * 2
