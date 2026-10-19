"""Backend entry point: settings are validated without echoing secret values."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from orchestration import __main__ as entry

_ENV = {
    "TLS_CERT_PATH": "cert.pem",
    "TLS_KEY_PATH": "key.pem",
    "BACKEND_AUTH_TOKEN": "tok",
    "AZURE_SPEECH_KEY": "speech-secret",
    "AZURE_SPEECH_REGION": "eastus",
    "TRIGGER_PHRASE": "board assistant",
}


def test_defaults_fill_optional_settings() -> None:
    config = entry.load_config(_ENV)
    assert config.host == "127.0.0.1"
    assert config.port == 8443
    assert config.model == "claude-opus-5-5"
    assert config.tts_voice == "en-US-JennyNeural"
    assert config.speech_language == "en-US"
    assert config.client_ca_path is None


def test_missing_settings_are_named_but_values_never_shown() -> None:
    env = dict(_ENV, AZURE_SPEECH_REGION="", TRIGGER_PHRASE="")
    with pytest.raises(entry.ConfigError) as error:
        entry.load_config(env)
    message = str(error.value)
    assert "TRIGGER_PHRASE" in message
    assert "AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT" in message
    assert "speech-secret" not in message


def test_bad_port_is_a_config_error() -> None:
    with pytest.raises(entry.ConfigError):
        entry.load_config(dict(_ENV, BACKEND_BIND_PORT="eighty"))


def test_env_file_skips_comments_and_lines_without_equals(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('# comment\n\nA=1\nB="two"\nwrapped-fragment\nC = three \n', encoding="utf-8")
    assert entry.read_env_file(path) == {"A": "1", "B": "two", "C": "three"}


def test_main_exits_2_on_missing_settings(monkeypatch, caplog) -> None:
    for name in list(_ENV) + ["AZURE_SPEECH_ENDPOINT"]:
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.ERROR):
        assert entry.main([]) == 2
    assert "missing settings" in caplog.text
