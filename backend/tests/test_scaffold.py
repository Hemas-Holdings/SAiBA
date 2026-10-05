"""Placeholder imports so CI proves the Sprint 1 backend layout is installable."""

import orchestration
import stt
import storage
import tts
import tools
from tools import external_tool, genie_tool


def test_backend_packages_importable() -> None:
    assert orchestration.__doc__
    assert stt.__doc__
    assert tts.__doc__
    assert storage.__doc__
    assert tools.__doc__
    assert genie_tool.__doc__
    assert external_tool.__doc__
