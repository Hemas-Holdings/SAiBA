"""Sprint 6: governed storage. No audio field, dev/test only, locked folder, clean writes."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storage import local as local_module
from storage.board import (
    InteractionRecord,
    PauseGap,
    PayloadRefused,
    SessionRecord,
    allowed_fields,
    payload_of,
)
from storage.local import LocalBoardStorage, StorageRefused, restrict_to_current_user

_T0 = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
_AUDIO_WORDS = ("audio", "pcm", "wav", "sound", "recording", "waveform")


def _interaction(**overrides: object) -> InteractionRecord:
    values: dict[str, object] = dict(
        session_id="session-1",
        sequence=1,
        answered_at=_T0,
        question="what was revenue",
        question_slice="good morning\nboard assistant what was revenue",
        answer="Revenue rose.",
        transcript="good morning\nboard assistant what was revenue\nAssistant: Revenue rose.",
    )
    values.update(overrides)
    return InteractionRecord(**values)  # type: ignore[arg-type]


def _session(**overrides: object) -> SessionRecord:
    values: dict[str, object] = dict(
        session_id="session-1",
        started_at=_T0,
        stopped_at=_T0 + timedelta(hours=1),
        interaction_count=1,
        pause_gaps=(PauseGap(_T0 + timedelta(minutes=5), _T0 + timedelta(minutes=9)),),
        transcript="good morning\n[paused 09:05–09:09]",
    )
    values.update(overrides)
    return SessionRecord(**values)  # type: ignore[arg-type]


def _no_lock(path: Path) -> None:
    del path


# ---------------------------------------------------------------- contract


def test_contract_has_no_audio_field() -> None:
    for record_type in (InteractionRecord, SessionRecord):
        for name in allowed_fields(record_type):
            assert not any(word in name.lower() for word in _AUDIO_WORDS), name


def test_interaction_payload_is_exactly_the_contract() -> None:
    payload = payload_of(_interaction())
    assert set(payload) == {
        "record_type",
        "schema_version",
        "session_id",
        "sequence",
        "answered_at",
        "question",
        "question_slice",
        "answer",
        "transcript",
    }
    assert payload["record_type"] == "interaction"
    assert payload["answered_at"] == "2026-10-26T09:00:00+00:00"


def test_session_payload_records_pause_times_not_speech() -> None:
    payload = payload_of(_session())
    assert payload["record_type"] == "session"
    assert payload["pause_gaps"] == [
        {"start": "2026-10-26T09:05:00+00:00", "end": "2026-10-26T09:09:00+00:00"}
    ]
    assert set(payload) == allowed_fields(SessionRecord)


@pytest.mark.parametrize("value", [b"\x01\x02", bytearray(b"\x01"), memoryview(b"\x01")])
def test_binary_data_is_refused_in_any_field(value: object) -> None:
    with pytest.raises(PayloadRefused, match="binary"):
        payload_of(_interaction(answer=value))


def test_naive_timestamps_and_odd_types_are_refused() -> None:
    with pytest.raises(PayloadRefused, match="timezone"):
        payload_of(_interaction(answered_at=datetime(2026, 1, 1)))
    with pytest.raises(PayloadRefused, match="unexpected type"):
        payload_of(_interaction(question=["a list"]))
    with pytest.raises(PayloadRefused, match="unexpected type"):
        payload_of(_interaction(sequence=True))


# ------------------------------------------------------------- local store


@pytest.mark.parametrize("environment", ["production", "", "staging", "Production"])
def test_local_storage_is_refused_outside_development_and_test(tmp_path: Path, environment: str) -> None:
    with pytest.raises(StorageRefused, match="development or test"):
        LocalBoardStorage(tmp_path, environment, lock_down=_no_lock)


def test_relative_folder_is_refused() -> None:
    with pytest.raises(StorageRefused, match="absolute"):
        LocalBoardStorage(Path("storage-data"), "development", lock_down=_no_lock)


def test_folder_is_created_and_locked_before_use(tmp_path: Path) -> None:
    locked: list[Path] = []
    root = tmp_path / "nested" / "storage-data"
    store = LocalBoardStorage(root, "test", lock_down=locked.append)
    assert root.is_dir()
    assert locked == [root]
    assert store.root == root


def test_lock_failure_refuses_storage(tmp_path: Path) -> None:
    def fail(path: Path) -> None:
        raise subprocess.CalledProcessError(5, ["icacls"])

    with pytest.raises(StorageRefused, match="could not lock"):
        LocalBoardStorage(tmp_path, "development", lock_down=fail)


async def test_records_are_written_as_json_per_session(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = LocalBoardStorage(tmp_path, "development", lock_down=_no_lock)
    with caplog.at_level(logging.DEBUG):
        await store.log_interaction(_interaction())
        await store.log_interaction(_interaction(sequence=2, answer="Costs fell."))
        await store.finalize_session(_session(interaction_count=2))
    folder = tmp_path / "session-1"
    assert sorted(item.name for item in folder.iterdir()) == [
        "interaction-0001.json",
        "interaction-0002.json",
        "session.json",
    ]
    first = json.loads((folder / "interaction-0001.json").read_text(encoding="utf-8"))
    assert first == payload_of(_interaction())
    session = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert session["interaction_count"] == 2
    assert "Revenue rose" not in caplog.text
    assert "what was revenue" not in caplog.text


async def test_bad_session_id_is_refused(tmp_path: Path) -> None:
    store = LocalBoardStorage(tmp_path, "development", lock_down=_no_lock)
    with pytest.raises(StorageRefused, match="session id"):
        await store.log_interaction(_interaction(session_id="../escape"))
    assert list(tmp_path.iterdir()) == []


async def test_failed_write_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalBoardStorage(tmp_path, "development", lock_down=_no_lock)

    def broken_replace(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(local_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        await store.log_interaction(_interaction())
    assert list((tmp_path / "session-1").iterdir()) == []


def test_lock_uses_icacls_for_the_current_user_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], check: bool, capture_output: bool) -> None:
        assert check and capture_output
        calls.append(args)

    monkeypatch.setattr(local_module.sys, "platform", "win32")
    monkeypatch.setattr(local_module.getpass, "getuser", lambda: "boarduser")
    monkeypatch.setattr(local_module.subprocess, "run", fake_run)
    restrict_to_current_user(tmp_path)
    assert calls == [["icacls", str(tmp_path), "/inheritance:r", "/grant:r", "boarduser:(OI)(CI)F"]]


def test_lock_uses_owner_only_mode_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[int] = []
    monkeypatch.setattr(local_module.sys, "platform", "linux")
    monkeypatch.setattr(local_module.os, "chmod", lambda path, mode: modes.append(mode))
    restrict_to_current_user(tmp_path)
    assert modes == [0o700]


async def test_real_lock_still_lets_the_owner_write(tmp_path: Path) -> None:
    store = LocalBoardStorage(tmp_path / "locked", "test")
    await store.finalize_session(_session())
    assert (tmp_path / "locked" / "session-1" / "session.json").is_file()
