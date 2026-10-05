# Board Knowledge Assistant

Desktop capture app plus a private backend that answers board-meeting questions. This repository is the Sprint 1 foundation from the phased development plan: repo layout, Python scaffolding, CI, and mixed audio capture.

Later sprints use local Start / Pause / Resume / Stop, continuous transcription of the unpaused session, and phrase-triggered analysis of the transcript through the question. The assistant reply is written back into that same transcript. See [`docs/session-model.md`](docs/session-model.md).

Planning docs are indexed from [`docs/`](docs/). Service catalog and required environment variables: [`docs/integrated-services.md`](docs/integrated-services.md). Copy [`.env.example`](.env.example) to `.env` locally; never commit real values.

## Layout

```
desktop-app/     Python desktop capture app (Sprint 1)
backend/         Orchestration, STT/TTS, tools, storage (later sprints)
infra/           IaC for VNet, Key Vault, endpoints (later sprints)
docs/            Security notes, consent placeholders, plan index
```

## Sprint 1 — Desktop capture foundation

Implemented in `desktop-app/capture/`:

- WASAPI loopback capture via `pyaudiowpatch` (Windows)
- Microphone capture via `sounddevice`
- In-memory mixer that combines both streams

Raw audio is never written to disk. The Sprint 1 gate is a live listen-through of the mixed stream, not a file recording.

### Setup

Python 3.11+. From `desktop-app/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Loopback capture installs `pyaudiowpatch` automatically on Windows (`pip install -e ".[windows]"`). On macOS/Linux, unit tests inject a mock backend; live WASAPI capture is not available.

### Live mixed session (Windows gate)

Use headphones to avoid feedback. This plays the mix locally and discards it; it does not save a recording.

```bash
cd desktop-app
python -m capture --duration 10
```

**Gate:** remote (loopback) speech and in-room (microphone) speech are both clearly audible in the mixed stream.

CI runs unit tests on every push and pull request (`.github/workflows/ci.yml`). Integration tests that open real devices are opt-in (`RUN_LIVE_AUDIO=1`) and excluded from CI.
