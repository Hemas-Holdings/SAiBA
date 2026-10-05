"""Segment B3: fail-closed trigger phrase matching on synthetic text."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from stt.phrase_detector import PhraseDetector, PhraseHit

_PHRASE = "board assistant"
_DETECTOR_PATH = Path(__file__).resolve().parents[1] / "stt" / "phrase_detector.py"
_SENTENCE = "uniquephrasezebra board assistant quiltingneedle"


@pytest.mark.parametrize(
    ("delta", "certain"),
    [
        ("please say board assistant now", True),
        ("Please, Board Assistant, now", True),
        ("hello room", False),
        ("board assis", False),
        ("board", False),
        ("boardassistant", False),
        ("myboard assistant", False),
        ("board assistants", False),
        ("[paused 14:02–14:11 board assistant]", False),
        ("[PAUSED 14:02-14:11 board assistant]", False),
        ("board assistant\n[paused 14:02–14:11]", True),
        ("hello [paused 14:02–14:11] board assistant", True),
        ("", False),
        ("   ", False),
    ],
)
def test_phrase_table(delta: str, certain: bool) -> None:
    hit = PhraseDetector(_PHRASE).observe(delta)
    if certain:
        assert hit == PhraseHit(_PHRASE)
    else:
        assert hit is None


def test_empty_configured_phrase_never_hits() -> None:
    for phrase in ("", "   "):
        detector = PhraseDetector(phrase)
        assert detector.observe(_SENTENCE) is None
        assert detector.observe("board assistant") is None


def test_phrase_split_across_deltas_hits_on_the_second() -> None:
    detector = PhraseDetector(_PHRASE)
    assert detector.observe("please board ") is None
    assert detector.observe("assistant what") == PhraseHit(_PHRASE)


def test_partial_word_does_not_stitch_into_a_hit() -> None:
    detector = PhraseDetector(_PHRASE)
    assert detector.observe("board assis") is None
    assert detector.observe("tant now") is None


def test_phrase_split_by_pause_gap_does_not_hit() -> None:
    detector = PhraseDetector(_PHRASE)
    assert detector.observe("board ") is None
    assert detector.observe("[paused 14:02–14:11]") is None
    assert detector.observe("assistant") is None


def test_phrase_inside_a_gap_that_spans_deltas_does_not_hit() -> None:
    detector = PhraseDetector(_PHRASE)
    assert detector.observe("[paused 14:02 board") is None
    assert detector.observe(" assistant still open") is None
    assert detector.observe(" inside] board assistant") == PhraseHit(_PHRASE)


def test_hit_does_not_stop_later_observation() -> None:
    detector = PhraseDetector(_PHRASE)
    assert detector.observe("board assistant") == PhraseHit(_PHRASE)
    assert detector.observe(" thanks everyone") is None
    assert detector.observe("ask board assistant again") == PhraseHit(_PHRASE)


def test_tail_stays_bounded_to_the_phrase() -> None:
    detector = PhraseDetector(_PHRASE)
    detector.observe(" ".join(f"alpha{index}" for index in range(400)))
    assert len(detector._tail) <= len(_PHRASE)
    assert "alpha0" not in detector._tail
    assert detector._tail.count("alpha") < 5


def test_one_word_phrase_does_not_keep_a_full_copy() -> None:
    detector = PhraseDetector("a")
    assert detector.observe("a") == PhraseHit("a")
    assert detector._tail == ""
    assert detector.observe("ba") is None


def test_logs_omit_transcript_text(caplog: pytest.LogCaptureFixture) -> None:
    detector = PhraseDetector(_PHRASE)
    with caplog.at_level(logging.INFO):
        hit = detector.observe(_SENTENCE)
    assert hit == PhraseHit(_PHRASE)
    assert "uniquephrasezebra" not in caplog.text
    assert "quiltingneedle" not in caplog.text
    assert "board assistant" not in caplog.text
    assert "certain=True" in caplog.text


def test_empty_phrase_logs_uncertain_without_text(caplog: pytest.LogCaptureFixture) -> None:
    detector = PhraseDetector("")
    with caplog.at_level(logging.INFO):
        assert detector.observe(_SENTENCE) is None
    assert "uniquephrasezebra" not in caplog.text
    assert "quiltingneedle" not in caplog.text
    assert "certain=False" in caplog.text


def test_detector_source_does_not_open_files() -> None:
    tree = ast.parse(_DETECTOR_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open"
