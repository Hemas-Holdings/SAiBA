"""Speech text for the board session.

`phrase_detector` matches a configured trigger phrase against transcript
deltas already in memory. A certain hit is returned to the caller; anything
uncertain is dropped.

`speech` turns continuous recognition into final utterances. `transcript`
keeps the in-memory meeting log, pause gaps, and the frozen question slice.
Audio is not written to disk. Transcript text is not logged at INFO or
below. See docs/session-model.md.
"""

from stt.phrase_detector import PhraseDetector, PhraseHit
from stt.speech import SpeechToText
from stt.transcript import SessionTranscript

__all__ = ["PhraseDetector", "PhraseHit", "SessionTranscript", "SpeechToText"]
