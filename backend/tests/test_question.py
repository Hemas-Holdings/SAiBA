"""Segment D3: the question after the trigger phrase in a frozen slice."""

from __future__ import annotations

import pytest

from orchestration.question import extract_question
from stt.phrase_detector import PhraseHit

_HIT = PhraseHit("board assistant")


def test_question_in_the_same_utterance() -> None:
    slice_ = "good morning\nBoard Assistant, what was Q3 revenue?"
    assert extract_question(slice_, _HIT) == "what was Q3 revenue?"


def test_question_spanning_two_utterances_and_a_gap() -> None:
    slice_ = "board assistant\n[paused 14:02–14:11]\nwhat was Q3 revenue"
    assert extract_question(slice_, _HIT) == "what was Q3 revenue"


def test_repeated_phrase_uses_the_last_one() -> None:
    slice_ = "board assistant what was revenue\nAssistant: twelve\nboard assistant and the margin"
    assert extract_question(slice_, _HIT) == "and the margin"


def test_assistant_turns_are_not_part_of_the_question() -> None:
    slice_ = "board assistant what was revenue\nAssistant: board assistant is ignored here"
    assert extract_question(slice_, _HIT) == "what was revenue"


@pytest.mark.parametrize(
    "slice_",
    [
        "board assistant",
        "board assistant ...?",
        "board assistant um, uh",
        "nothing relevant here",
        "",
    ],
)
def test_phrase_with_no_question_is_none(slice_: str) -> None:
    assert extract_question(slice_, _HIT) is None


def test_partial_words_do_not_match() -> None:
    assert extract_question("onboard assistants what now", _HIT) is None


def test_empty_phrase_is_none() -> None:
    assert extract_question("board assistant what", PhraseHit("  ")) is None
