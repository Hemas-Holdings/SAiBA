"""In-memory session transcript for one meeting.

Holds meeting text, assistant turns, and pause gap markers. Meeting text is
refused while a pause is open. A phrase hit freezes a slice through the end
of the question; later meeting text stays on the live transcript and out of
that slice. Nothing here is written to disk or logged. See
docs/session-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stt.phrase_detector import PhraseHit


@dataclass(frozen=True)
class _Entry:
    kind: str
    text: str


class SessionTranscript:
    """Ordered log of one server session. Not a recording."""

    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self._paused = False
        self._pause_start: datetime | None = None
        self._previous_utterance = ""
        self._waiting_hit: PhraseHit | None = None
        self._slices: list[tuple[PhraseHit, str]] = []

    @property
    def is_paused(self) -> bool:
        return self._paused

    def append_meeting(self, text: str) -> None:
        """Append one final utterance. Dropped while paused or when empty."""

        if self._paused or not text.strip():
            return
        self._entries.append(_Entry("meeting", text.strip()))

    def begin_pause(self, start: datetime) -> None:
        """Stop accepting meeting text until `end_pause`."""

        _require_aware(start)
        if self._paused:
            raise RuntimeError("pause is already open")
        self._paused = True
        self._pause_start = start

    def end_pause(self, end: datetime) -> None:
        """Write the gap marker and accept meeting text again."""

        _require_aware(end)
        if self._pause_start is None:
            raise RuntimeError("pause is not open")
        start = self._pause_start
        self._paused = False
        self._pause_start = None
        self.append_pause_gap(start, end)

    def append_pause_gap(self, start: datetime, end: datetime) -> None:
        """Record omitted time. This does not add speech."""

        _require_aware(start)
        _require_aware(end)
        if end < start:
            raise ValueError("pause end is before start")
        marker = f"[paused {start:%H:%M}\u2013{end:%H:%M}]"
        self._entries.append(_Entry("gap", marker))

    def append_assistant(self, text: str) -> None:
        """Append an assistant turn. Refused while paused. Not a speaker label."""

        if self._paused or not text.strip():
            return
        self._entries.append(_Entry("assistant", text.strip()))

    def observe_utterance(self, text: str, hit: PhraseHit | None) -> None:
        """Append a final utterance and maybe freeze the question slice.

        Recognition is not stopped here. A gap does not close a question.
        """

        if self._paused or not text.strip():
            return
        self.append_meeting(text)
        if self._waiting_hit is not None:
            self._freeze(self._waiting_hit)
            self._waiting_hit = None
            self._previous_utterance = text
            return
        if hit is not None:
            if _closes_now(self._previous_utterance, text, hit.phrase):
                self._freeze(hit)
            else:
                self._waiting_hit = hit
        self._previous_utterance = text

    def slice_through_question(self, phrase_hit: PhraseHit) -> str | None:
        """Return the frozen slice, or None if that question has not ended."""

        found: str | None = None
        for hit, text in self._slices:
            if hit == phrase_hit:
                found = text
        return found

    def render(self) -> str:
        """The live transcript, including text after any frozen slice."""

        lines: list[str] = []
        for entry in self._entries:
            if entry.kind == "assistant":
                lines.append(f"Assistant: {entry.text}")
            else:
                lines.append(entry.text)
        return "\n".join(lines)

    def _freeze(self, hit: PhraseHit) -> None:
        self._slices.append((hit, self.render()))


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")


def _words(text: str) -> list[str]:
    characters: list[str] = []
    for character in text.casefold():
        if character.isalnum() or character.isspace():
            characters.append(character)
        else:
            characters.append(" ")
    return "".join(characters).split()


def _closes_now(previous: str, utterance: str, phrase: str) -> bool:
    """True when this utterance continues past the phrase."""

    phrase_words = _words(phrase)
    if not phrase_words:
        return False
    window = _words(f"{previous} {utterance}")
    last: int | None = None
    limit = len(window) - len(phrase_words) + 1
    for index in range(limit):
        if window[index : index + len(phrase_words)] == phrase_words:
            last = index
    if last is None:
        return False
    return last + len(phrase_words) < len(window)
