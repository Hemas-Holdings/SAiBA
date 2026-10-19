"""Word-level helpers shared by the transcript and question extraction.

Pure functions on in-memory text. Nothing here logs or stores text.
"""

from __future__ import annotations


def words(text: str) -> list[str]:
    """Casefolded words; punctuation splits words like whitespace does."""

    characters: list[str] = []
    for character in text.casefold():
        if character.isalnum() or character.isspace():
            characters.append(character)
        else:
            characters.append(" ")
    return "".join(characters).split()


def last_phrase_end(window: list[str], phrase_words: list[str]) -> int | None:
    """Index just past the last whole-word match of `phrase_words`, or None."""

    if not phrase_words:
        return None
    last: int | None = None
    limit = len(window) - len(phrase_words) + 1
    for index in range(limit):
        if window[index : index + len(phrase_words)] == phrase_words:
            last = index + len(phrase_words)
    return last
