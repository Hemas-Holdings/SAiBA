"""Certain/uncertain trigger-phrase matching on transcript text.

Observes text deltas that already exist in memory. A hit means the
configured phrase was present as whole words. Partial phrases, an empty
configuration, and text inside a pause gap marker return nothing, so
analysis stays disarmed. The rolling tail is only long enough to see a
phrase split across two deltas. This module does not write the transcript
to disk or include it in logs. See docs/session-model.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MARKER = "[paused"


@dataclass(frozen=True)
class PhraseHit:
    """A certain match of the configured phrase. Holds no transcript text."""

    phrase: str


class PhraseDetector:
    """Fail-closed matcher. `observe` returns `None` unless the phrase is certain."""

    def __init__(self, phrase: str) -> None:
        self._phrase = phrase.strip()
        self._phrase_norm = _normalize(self._phrase)
        self._tail = ""
        self._inside_gap = False

    @property
    def phrase(self) -> str:
        """The configured phrase. Configuration, not transcript text."""

        return self._phrase

    def observe(self, transcript_delta: str) -> PhraseHit | None:
        """Return a hit when `transcript_delta` completes the configured phrase."""

        if not self._phrase_norm:
            logger.info("phrase detection ran certain=%s", False)
            return None

        hit = False
        for part in self._segments(transcript_delta):
            if part is None:
                self._tail = ""
                continue
            normalized = _normalize(part)
            if not normalized:
                continue
            combined = f"{self._tail} {normalized}".strip() if self._tail else normalized
            if _contains_phrase(combined, self._phrase_norm):
                hit = True
            keep = len(self._phrase_norm) - 1
            self._tail = combined[-keep:] if keep > 0 else ""

        logger.info("phrase detection ran certain=%s", hit)
        return PhraseHit(self._phrase) if hit else None

    def _segments(self, delta: str) -> list[str | None]:
        """Split `delta` into visible text and gap markers. `None` is a gap."""

        parts: list[str | None] = []
        if self._inside_gap:
            end = delta.find("]")
            if end == -1:
                parts.append(None)
                return parts
            self._inside_gap = False
            parts.append(None)
            delta = delta[end + 1 :]

        cursor = 0
        while cursor < len(delta):
            start = _find_marker(delta, cursor)
            if start == -1:
                parts.append(delta[cursor:])
                break
            if start > cursor:
                parts.append(delta[cursor:start])
            end = delta.find("]", start)
            parts.append(None)
            if end == -1:
                self._inside_gap = True
                break
            cursor = end + 1
        return parts


def _normalize(text: str) -> str:
    chars: list[str] = []
    for char in text.casefold():
        if char.isalnum() or char.isspace():
            chars.append(char)
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    pattern = r"(?<!\S)" + re.escape(phrase) + r"(?!\S)"
    return re.search(pattern, text) is not None


def _find_marker(text: str, start: int) -> int:
    limit = len(text) - len(_MARKER)
    index = start
    while index <= limit:
        if text[index : index + len(_MARKER)].casefold() == _MARKER:
            return index
        index += 1
    return -1
