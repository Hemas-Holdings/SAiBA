"""Run the backend: capture receiver, STT, orchestration, TTS, playback.

    python -m orchestration [--env-file .env] [--print-transcript]

This is the only place that reads the environment; every class takes its
settings as arguments. Each capture connection gets its own transcript,
recognizer and phrase detector. Audio is never written to disk. Transcript
text is written only through governed storage, and only when
LOCAL_STORAGE_DIR is set (development and test; see storage/local.py).
The transcript is printed to the console at the end of a session only with
`--print-transcript` (for the Sprint 5 gate); it is never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.board import BoardStorage
    from tools.external_tool.agent import WebResearcher

logger = logging.getLogger("orchestration")

_REQUIRED = (
    "TLS_CERT_PATH",
    "TLS_KEY_PATH",
    "BACKEND_AUTH_TOKEN",
    "AZURE_SPEECH_KEY",
    "TRIGGER_PHRASE",
)


class ConfigError(Exception):
    """Required settings are missing. The message names them, never their values."""


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    cert_path: str
    key_path: str
    client_ca_path: str | None
    token: str
    speech_key: str
    speech_region: str
    speech_endpoint: str | None
    speech_language: str
    tts_voice: str
    trigger_phrase: str
    model: str
    storage_dir: str | None = None
    environment: str = ""
    external_enabled: bool = False
    external_domains: tuple[str, ...] = ()
    external_max_uses: int = 5
    external_model: str = ""


def load_config(env: Mapping[str, str]) -> Config:
    from orchestration.orchestrator import DEFAULT_MODEL
    from orchestration.receive import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT
    from tts.speech import DEFAULT_VOICE

    missing = [name for name in _REQUIRED if not env.get(name, "").strip()]
    if not env.get("AZURE_SPEECH_REGION", "").strip() and not env.get("AZURE_SPEECH_ENDPOINT", "").strip():
        missing.append("AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT")
    if missing:
        raise ConfigError("missing settings: " + ", ".join(missing))
    try:
        port = int(env.get("BACKEND_BIND_PORT") or DEFAULT_BIND_PORT)
    except ValueError as exc:
        raise ConfigError("BACKEND_BIND_PORT must be a number") from exc
    return Config(
        host=env.get("BACKEND_BIND_HOST") or DEFAULT_BIND_HOST,
        port=port,
        cert_path=env["TLS_CERT_PATH"].strip(),
        key_path=env["TLS_KEY_PATH"].strip(),
        client_ca_path=(env.get("TLS_CLIENT_CA_PATH") or "").strip() or None,
        token=env["BACKEND_AUTH_TOKEN"].strip(),
        speech_key=env["AZURE_SPEECH_KEY"].strip(),
        speech_region=(env.get("AZURE_SPEECH_REGION") or "").strip(),
        speech_endpoint=(env.get("AZURE_SPEECH_ENDPOINT") or "").strip() or None,
        speech_language=(env.get("AZURE_SPEECH_LANGUAGE") or "").strip() or "en-US",
        tts_voice=(env.get("AZURE_SPEECH_TTS_VOICE") or "").strip() or DEFAULT_VOICE,
        trigger_phrase=env["TRIGGER_PHRASE"].strip(),
        model=(env.get("ANTHROPIC_ORCHESTRATION_MODEL") or "").strip() or DEFAULT_MODEL,
        storage_dir=(env.get("LOCAL_STORAGE_DIR") or "").strip() or None,
        environment=(env.get("ENVIRONMENT") or "").strip().lower(),
        **_external_settings(env),
    )


def _external_settings(env: Mapping[str, str]) -> dict[str, object]:
    """Opt-in web search for the external tool. Off unless EXTERNAL_SEARCH_ENABLED=true."""

    from tools.external_tool.agent import DEFAULT_MAX_SEARCHES, DEFAULT_MODEL, parse_domains

    flag = (env.get("EXTERNAL_SEARCH_ENABLED") or "").strip().lower()
    if flag not in ("", "false", "true"):
        raise ConfigError("EXTERNAL_SEARCH_ENABLED must be true or false")
    enabled = flag == "true"
    try:
        domains = parse_domains(env.get("EXTERNAL_SEARCH_ALLOWED_DOMAINS") or "")
    except ValueError as exc:
        raise ConfigError(f"EXTERNAL_SEARCH_ALLOWED_DOMAINS: {exc}") from exc
    if enabled and not domains:
        raise ConfigError("EXTERNAL_SEARCH_ALLOWED_DOMAINS is required when external search is enabled")
    try:
        max_uses = int(env.get("EXTERNAL_SEARCH_MAX_USES") or DEFAULT_MAX_SEARCHES)
    except ValueError as exc:
        raise ConfigError("EXTERNAL_SEARCH_MAX_USES must be a number") from exc
    if not 1 <= max_uses <= 20:
        raise ConfigError("EXTERNAL_SEARCH_MAX_USES must be between 1 and 20")
    return {
        "external_enabled": enabled,
        "external_domains": domains,
        "external_max_uses": max_uses,
        "external_model": (env.get("ANTHROPIC_EXTERNAL_MODEL") or "").strip() or DEFAULT_MODEL,
    }


def build_external(config: Config) -> WebResearcher | None:
    """The external web researcher, or None to keep the stub."""

    if not config.external_enabled:
        logger.info("external search off: using the stub external tool")
        return None
    from tools.external_tool.agent import WebResearcher

    logger.info("external search on domains=%d", len(config.external_domains))
    return WebResearcher(
        config.external_domains,
        model=config.external_model,
        max_searches=config.external_max_uses,
    )


def build_storage(config: Config) -> BoardStorage | None:
    """The governed store, or None when LOCAL_STORAGE_DIR is not set."""

    if config.storage_dir is None:
        logger.info("governed storage off: LOCAL_STORAGE_DIR is not set")
        return None
    from storage.local import LocalBoardStorage, StorageRefused

    try:
        return LocalBoardStorage(Path(config.storage_dir), config.environment)
    except StorageRefused as error:
        raise ConfigError(str(error)) from error


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Comments, blanks and lines without `=` are skipped."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


async def serve(config: Config, print_transcript: bool = False, storage: BoardStorage | None = None) -> None:
    from orchestration.live import LiveSession
    from orchestration.orchestrator import Orchestrator
    from orchestration.playback import PlaybackHub
    from orchestration.receive import ReceiveServer, TokenVerifier, build_server_ssl_context
    from stt.phrase_detector import PhraseDetector
    from stt.speech import AzureSpeechAdapter, SpeechToText
    from stt.transcript import SessionTranscript
    from tts.speech import AzureSpeechSynthesisAdapter, TextToSpeech

    orchestrator = Orchestrator(model=config.model, external=build_external(config))
    hub = PlaybackHub(
        TextToSpeech(
            AzureSpeechSynthesisAdapter(
                config.speech_key,
                config.speech_region,
                voice=config.tts_voice,
                endpoint=config.speech_endpoint,
            )
        )
    )

    def on_closed(transcript: SessionTranscript) -> None:
        if print_transcript:
            print("----- session transcript -----", flush=True)
            print(transcript.render(), flush=True)
            print("------------------------------", flush=True)

    def new_session() -> LiveSession:
        speech = SpeechToText(
            AzureSpeechAdapter(
                config.speech_key,
                config.speech_region,
                language=config.speech_language,
                endpoint=config.speech_endpoint,
            )
        )
        return LiveSession(
            speech,
            SessionTranscript(),
            PhraseDetector(config.trigger_phrase),
            orchestrator=orchestrator,
            delivery=hub,
            on_closed=on_closed,
            storage=storage,
            events=hub,
        )

    server = ReceiveServer(
        None,
        TokenVerifier(config.token),
        build_server_ssl_context(config.cert_path, config.key_path, config.client_ca_path),
        host=config.host,
        port=config.port,
        sink_factory=new_session,
        playback=hub,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopping.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: KeyboardInterrupt ends asyncio.run instead.
    await server.start()
    logger.info("backend listening port=%d", server.bound_port)
    try:
        await stopping.wait()
    finally:
        await server.stop()
        await hub.shutdown()
        logger.info("backend stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Board Knowledge Assistant backend")
    parser.add_argument("--env-file", type=Path, help="Read settings from this file before the environment")
    parser.add_argument(
        "--print-transcript",
        action="store_true",
        help="Print each session's transcript to the console when it closes (gate only)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env: dict[str, str] = {}
    if args.env_file is not None:
        env.update(read_env_file(args.env_file))
    env.update({key: value for key, value in os.environ.items() if value})
    try:
        config = load_config(env)
        storage = build_storage(config)
    except ConfigError as error:
        logger.error("%s", error)
        return 2
    if env.get("ANTHROPIC_API_KEY"):
        os.environ.setdefault("ANTHROPIC_API_KEY", env["ANTHROPIC_API_KEY"])
    try:
        asyncio.run(serve(config, args.print_transcript, storage))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
