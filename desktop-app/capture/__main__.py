"""CLI for the Sprint 1 mixed-capture gate.

Plays the mix locally. Does not write a recording.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Listen to the mixed WASAPI loopback + microphone stream. "
            "Audio is played locally and discarded; it is never written to disk."
        )
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Seconds to listen (default: 10)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if sys.platform != "win32":
        logging.error(
            "Live WASAPI loopback requires Windows. Unit tests on this platform "
            "inject a mock loopback backend."
        )
        return 1

    from capture.listen import SoundDevicePlaybackSink, run_listen_through

    try:
        asyncio.run(run_listen_through(args.duration, SoundDevicePlaybackSink()))
    except KeyboardInterrupt:
        logging.info("listen-through interrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
