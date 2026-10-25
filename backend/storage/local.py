"""Local-folder governed storage, for development and test only.

Writes board-session records (transcript text, questions, slices, answers,
timestamps; never audio) as JSON files under one folder on the backend
machine. This replaces Unity Catalog until a workspace exists, by explicit
decision recorded in docs/security-notes.md. Safeguards:

- refused unless the environment is `development` or `test`
- the folder must be an absolute path set by the operator; there is no default
- the folder is locked to the current user (Windows ACL, or mode 0700) before
  anything is written; if that fails, storage is refused
- each file is written to a temporary name and renamed, so no partial record
  is left behind

Layout: `<root>/<session_id>/interaction-0001.json`, ..., `session.json`.
Nothing here logs record text.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from storage.board import InteractionRecord, SessionRecord, payload_of

logger = logging.getLogger(__name__)

ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StorageRefused(Exception):
    """Local storage cannot be used with these settings. The message names no record text."""


def restrict_to_current_user(path: Path) -> None:
    """Allow only the current user to read or write the folder and what it holds."""

    if sys.platform == "win32":
        user = getpass.getuser()
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
            check=True,
            capture_output=True,
        )
    else:
        os.chmod(path, 0o700)


class LocalBoardStorage:
    """Writes governed records to a locked local folder."""

    def __init__(
        self,
        root: Path,
        environment: str,
        lock_down: Callable[[Path], None] = restrict_to_current_user,
    ) -> None:
        if environment not in ALLOWED_ENVIRONMENTS:
            raise StorageRefused("local storage is allowed only when ENVIRONMENT is development or test")
        if not root.is_absolute():
            raise StorageRefused("LOCAL_STORAGE_DIR must be an absolute path")
        try:
            root.mkdir(parents=True, exist_ok=True)
            lock_down(root)
        except (OSError, subprocess.CalledProcessError) as error:
            raise StorageRefused(f"could not lock the storage folder ({type(error).__name__})") from error
        self._root = root
        logger.info("local governed storage ready")

    @property
    def root(self) -> Path:
        return self._root

    async def log_interaction(self, record: InteractionRecord) -> None:
        payload = payload_of(record)
        name = f"interaction-{record.sequence:04d}.json"
        await asyncio.to_thread(self._write, record.session_id, name, payload)
        logger.info("interaction stored sequence=%d", record.sequence)

    async def finalize_session(self, record: SessionRecord) -> None:
        payload = payload_of(record)
        await asyncio.to_thread(self._write, record.session_id, "session.json", payload)
        logger.info("session stored interactions=%d", record.interaction_count)

    def _write(self, session_id: str, name: str, payload: dict[str, object]) -> None:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise StorageRefused("session id is not allowed")
        folder = self._root / session_id
        folder.mkdir(exist_ok=True)
        if sys.platform != "win32":
            os.chmod(folder, 0o700)
        target = folder / name
        temporary = folder / f".{name}.tmp"
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
