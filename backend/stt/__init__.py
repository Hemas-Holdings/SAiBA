"""Speech text for the board session.

`phrase_detector` matches a configured trigger phrase against transcript
deltas already in memory. A certain hit is returned to the caller; anything
uncertain is dropped. It does not log or store the transcript.

Azure Speech continuous recognition is Sprint 3 and is not in this package
yet. Audio is not written to disk. Transcript text is not logged at INFO
or below. See docs/session-model.md.
"""

from stt.phrase_detector import PhraseDetector, PhraseHit

__all__ = ["PhraseDetector", "PhraseHit"]
