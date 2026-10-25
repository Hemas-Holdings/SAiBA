"""In-memory session transcript for one meeting.

Holds meeting text, assistant turns, and pause gap markers. Meeting text is
refused while a pause is open. A phrase hit freezes a slice through the end
of the question; later meeting text stays on the live transcript and out of
that slice. Nothing here is written to disk or logged; the governed
storage write path (`storage/`) receives the rendered text when a question
is answered and at Stop, after which `clear` drops it. See
docs/session-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stt.phrase_detector import PhraseHit
from stt.text import last_phrase_end, words


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
        self._gaps: list[tuple[datetime, datetime]] = []

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
        self._gaps.append((start, end))

    def pause_gaps(self) -> list[tuple[datetime, datetime]]:
        """Start and end of every closed pause, in order. Times only, no speech."""

        return list(self._gaps)

    def clear(self) -> None:
        """Drop everything held for this session. Used after the final governed write."""

        self._entries.clear()
        self._slices.clear()
        self._gaps.clear()
        self._previous_utterance = ""
        self._waiting_hit = None

    def append_assistant(self, text: str) -> None:
        """Append an assistant turn. Refused while paused. Not a speaker label."""

        if self._paused or not text.strip():
            return
        self._entries.append(_Entry("assistant", text.strip()))

    def observe_utterance(self, text: str, hit: PhraseHit | None) -> PhraseHit | None:
        """Append a final utterance and maybe freeze the question slice.

        Returns the hit whose slice was frozen by this utterance, else None.
        Recognition is not stopped here. A gap does not close a question.
        """

        if self._paused or not text.strip():
            return None
        self.append_meeting(text)
        frozen: PhraseHit | None = None
        if self._waiting_hit is not None:
            frozen = self._waiting_hit
            self._freeze(frozen)
            self._waiting_hit = None
            self._previous_utterance = text
            return frozen
        if hit is not None:
            if _closes_now(self._previous_utterance, text, hit.phrase):
                frozen = hit
                self._freeze(hit)
            else:
                self._waiting_hit = hit
        self._previous_utterance = text
        return frozen

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


def _closes_now(previous: str, utterance: str, phrase: str) -> bool:
    """True when this utterance continues past the phrase."""

    window = words(f"{previous} {utterance}")
    end = last_phrase_end(window, words(phrase))
    if end is None:
        return False
    return end < len(window)
