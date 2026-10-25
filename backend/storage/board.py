"""Write contract for governed board-session storage.

Handles board-meeting text: the running session transcript, each question,
the transcript slice it was asked against, and the answer. Records go only
to a `BoardStorage` implementation (a local folder in development and test;
Unity Catalog later). There is no audio field. `payload_of` builds the exact
dictionary that may be written and refuses anything that is not text or a
timestamp, so audio bytes cannot ride along. Nothing here logs record text.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Protocol, Union

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PauseGap:
    """When a pause started and ended. Times only; paused speech is never captured."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class InteractionRecord:
    """One answered question, written after the answer is on the transcript."""

    session_id: str
    sequence: int
    answered_at: datetime
    question: str
    question_slice: str
    answer: str
    transcript: str


@dataclass(frozen=True)
class SessionRecord:
    """The finished session, written once at Stop."""

    session_id: str
    started_at: datetime
    stopped_at: datetime
    interaction_count: int
    pause_gaps: tuple[PauseGap, ...]
    transcript: str


Record = Union[InteractionRecord, SessionRecord]


class BoardStorage(Protocol):
    """Where governed records go. Implementations must not log record text."""

    async def log_interaction(self, record: InteractionRecord) -> None: ...

    async def finalize_session(self, record: SessionRecord) -> None: ...


class PayloadRefused(ValueError):
    """A record held something the contract does not allow."""


def allowed_fields(record_type: type) -> frozenset[str]:
    """The only keys a written payload of this record type may have."""

    return frozenset(item.name for item in fields(record_type)) | {"record_type", "schema_version"}


def payload_of(record: Record) -> dict[str, object]:
    """The exact dictionary to write. Text, integers and ISO timestamps only."""

    payload: dict[str, object] = {
        "record_type": "interaction" if isinstance(record, InteractionRecord) else "session",
        "schema_version": SCHEMA_VERSION,
    }
    for item in fields(record):
        payload[item.name] = _plain(getattr(record, item.name), item.name)
    if set(payload) != allowed_fields(type(record)):
        raise PayloadRefused("payload fields do not match the contract")
    return payload


def _plain(value: object, name: str) -> object:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise PayloadRefused(f"field {name} holds binary data")
    if isinstance(value, bool):
        raise PayloadRefused(f"field {name} has an unexpected type")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PayloadRefused(f"field {name} is not timezone-aware")
        return value.isoformat()
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, tuple) and all(isinstance(gap, PauseGap) for gap in value):
        return [{"start": _plain(gap.start, name), "end": _plain(gap.end, name)} for gap in value]
    raise PayloadRefused(f"field {name} has an unexpected type")
