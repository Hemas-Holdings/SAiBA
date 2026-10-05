"""CLI entrypoint tests. Live devices are never opened here."""

from __future__ import annotations

import sys

from capture.__main__ import main


def test_main_refuses_non_windows(monkeypatch: object) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")  # type: ignore[attr-defined]
    assert main(["--duration", "1"]) == 1
