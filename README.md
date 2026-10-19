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

## Sprint 5 — Spoken answers (round-trip gate)

Two processes on one Windows test machine. Settings come from the environment or `--env-file` (see `.env.example`).

Create a local certificate for `127.0.0.1` (self-signed is fine for the gate), then set `TLS_CERT_PATH`, `TLS_KEY_PATH`, `TLS_CA_CERT_PATH` (the same certificate, so the desktop trusts it), `BACKEND_AUTH_TOKEN`, `BACKEND_BASE_URL=wss://127.0.0.1:8443`, `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `TRIGGER_PHRASE` and `ANTHROPIC_API_KEY`.

```bash
# console 1: backend (prints the transcript when the session closes)
cd backend
python -m orchestration --env-file ../.env --print-transcript

# console 2: local controls — s start, p pause, r resume, q stop (each + Enter)
cd desktop-app
python -m ui --env-file ../.env          # add --mic-only off Windows
```

**Gate:** Start, speak, say the trigger phrase and a question; the answer is spoken (once, no echo re-trigger) and appears as an `Assistant:` line. Pause, speak, Resume: the paused speech is missing and a `[paused …]` marker is present. Press Stop while an answer is playing: it finishes. Nothing is written to disk.

Optional live checks: `pytest -m integration tests/test_tts_live.py` (Azure TTS through the speakers, `RUN_LIVE_AUDIO=1`) and `pytest -m integration tests/test_orchestration_integration.py` (real Agent SDK).

