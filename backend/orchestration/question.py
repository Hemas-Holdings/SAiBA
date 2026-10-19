"""Pull the question out of a frozen transcript slice.

The question is the meeting text after the last trigger phrase in the slice.
Gap markers and earlier assistant turns are not part of it. A phrase with
nothing after it but filler or punctuation is not a question. Nothing here
logs or stores text. See docs/session-model.md.
"""

from __future__ import annotations

import re

from stt.phrase_detector import PhraseHit
from stt.text import words

_GAP = re.compile(r"\[paused[^\]]*\]", re.IGNORECASE)
_ASSISTANT = "Assistant: "
_FILLER = frozenset({"um", "uh", "er", "erm", "ah", "hmm", "mm", "okay", "ok", "so"})


def extract_question(slice: str, phrase_hit: PhraseHit) -> str | None:
    """Return the question after the last phrase in `slice`, or None."""

    phrase_words = words(phrase_hit.phrase)
    if not phrase_words:
        return None
    lines = [line for line in slice.splitlines() if not line.startswith(_ASSISTANT)]
    meeting = _GAP.sub(" ", " ".join(lines))
    match = _last_match(meeting, phrase_words)
    if match is None:
        return None
    question = " ".join(meeting[match:].split()).lstrip(" ,.;:!?-–—")
    remaining = words(question)
    if not remaining or all(word in _FILLER for word in remaining):
        return None
    return question


def _last_match(text: str, phrase_words: list[str]) -> int | None:
    """Character offset just past the last whole-word phrase match."""

    joined = r"[^\w]+".join(re.escape(word) for word in phrase_words)
    pattern = re.compile(r"(?<!\w)" + joined + r"(?!\w)", re.IGNORECASE)
    end: int | None = None
    for found in pattern.finditer(text):
        end = found.end()
    return end
