"""Local console for the Sprint 5 gate.

    python -m ui [--env-file .env] [--mic-only]

Live loopback capture needs Windows; `--mic-only` replaces it with silence
so the round trip can be tried elsewhere.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from ui.console import Console, ConfigError, build_streamer, load_config


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Board Knowledge Assistant local console")
    parser.add_argument("--env-file", type=Path, help="Read settings from this file before the environment")
    parser.add_argument("--mic-only", action="store_true", help="Capture the microphone only (no WASAPI loopback)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env: dict[str, str] = {}
    if args.env_file is not None:
        env.update(_read_env_file(args.env_file))
    env.update({key: value for key, value in os.environ.items() if value})
    try:
        config = load_config(env)
    except ConfigError as error:
        logging.error("%s", error)
        return 2
    if sys.platform != "win32" and not args.mic_only:
        logging.error("Live WASAPI loopback requires Windows. Use --mic-only on this platform.")
        return 1
    streamer, cleanup = build_streamer(config, mic_only=args.mic_only)
    try:
        asyncio.run(Console(streamer, input).run())
    except KeyboardInterrupt:
        return 130
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
