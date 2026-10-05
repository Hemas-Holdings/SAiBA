"""Segment D2: in-memory transcript, pause gaps, and question slices."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stt.phrase_detector import PhraseHit
from stt.transcript import SessionTranscript

_HIT = PhraseHit("board assistant")
_T1 = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
_T2 = datetime(2026, 1, 1, 14, 11, tzinfo=timezone.utc)


def test_unpaused_speech_is_stored_and_paused_speech_is_only_a_gap() -> None:
    transcript = SessionTranscript()
    transcript.append_meeting("the quarter was steady")
    transcript.begin_pause(_T1)
    transcript.append_meeting("secret side conversation")
    transcript.append_assistant("should not land during pause")
    transcript.end_pause(_T2)
    rendered = transcript.render()
    assert "the quarter was steady" in rendered
    assert "secret side conversation" not in rendered
    assert "should not land" not in rendered
    assert "[paused 14:02\u201314:11]" in rendered
    transcript.append_meeting("after the break")
    assert "after the break" in transcript.render()


def test_slice_closes_when_the_question_is_in_the_same_utterance() -> None:
    transcript = SessionTranscript()
    transcript.append_meeting("earlier discussion")
    transcript.append_assistant("the margin was 12 percent")
    transcript.observe_utterance("board assistant what about next year", _HIT)
    frozen = transcript.slice_through_question(_HIT)
    assert frozen is not None
    assert "Assistant: the margin was 12 percent" in frozen
    assert "what about next year" in frozen
    transcript.append_meeting("chatter after the question")
    assert transcript.slice_through_question(_HIT) == frozen
    assert "chatter after the question" in transcript.render()
    assert "chatter after the question" not in frozen


def test_slice_waits_for_the_next_utterance_when_the_phrase_ends_one() -> None:
    transcript = SessionTranscript()
    transcript.observe_utterance("board assistant", _HIT)
    assert transcript.slice_through_question(_HIT) is None
    transcript.observe_utterance("what was revenue", None)
    frozen = transcript.slice_through_question(_HIT)
    assert frozen is not None
    assert "what was revenue" in frozen
    transcript.append_meeting("later")
    assert "later" not in (transcript.slice_through_question(_HIT) or "")


def test_pause_gap_does_not_close_the_question_or_add_speech() -> None:
    transcript = SessionTranscript()
    transcript.observe_utterance("board assistant", _HIT)
    transcript.begin_pause(_T1)
    transcript.append_meeting("whispered secret")
    transcript.end_pause(_T2)
    assert transcript.slice_through_question(_HIT) is None
    assert "whispered secret" not in transcript.render()
    transcript.observe_utterance("what was revenue", None)
    frozen = transcript.slice_through_question(_HIT)
    assert frozen is not None
    assert "what was revenue" in frozen
    assert "whispered secret" not in frozen
    assert "[paused 14:02\u201314:11]" in frozen


def test_phrase_split_across_utterances_with_a_question_closes() -> None:
    transcript = SessionTranscript()
    transcript.observe_utterance("please board", None)
    transcript.observe_utterance("assistant what is the margin", _HIT)
    frozen = transcript.slice_through_question(_HIT)
    assert frozen is not None
    assert "what is the margin" in frozen


def test_naive_timestamp_is_rejected() -> None:
    transcript = SessionTranscript()
    with pytest.raises(ValueError, match="timezone-aware"):
        transcript.append_pause_gap(datetime(2026, 1, 1, 14, 2), _T2)
