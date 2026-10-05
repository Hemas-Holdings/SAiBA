"""Desktop audio capture: loopback, microphone, and in-memory mix.

Handles raw PCM from the WASAPI loopback device and the microphone. Mixed
chunks stay in memory and are yielded to the caller (listen-through now;
session streaming in later sprints). Pause (Sprint 2+) must stop this
package from yielding chunks. This package never writes audio to disk.
"""

from capture.audio_format import CHUNK_BYTES, FRAMES_PER_CHUNK, SAMPLE_RATE
from capture.listen import run_listen_through
from capture.loopback import LoopbackCapture
from capture.microphone import MicCapture
from capture.mixer import AudioMixer

__all__ = [
    "CHUNK_BYTES",
    "FRAMES_PER_CHUNK",
    "SAMPLE_RATE",
    "AudioMixer",
    "LoopbackCapture",
    "MicCapture",
    "run_listen_through",
]
