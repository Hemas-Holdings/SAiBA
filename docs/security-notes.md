# Security notes

Living notes for the Board Knowledge Assistant. Expand as later sprints add transport, STT/TTS, and storage. Do not treat this file as a complete threat model. Session behavior is defined in [session-model.md](session-model.md).

## Data handling (Sprint 1)

- Raw audio stays in memory in `desktop-app/capture/`. It is never written to disk, including for debugging.
- Mixed PCM is played locally for the capture gate, then discarded.
- Logs may record that capture started or stopped. They must not include audio bytes or transcript text.
- Unit tests use synthetic PCM and mocked devices. They must not call real speech APIs or store fixtures of real meeting audio.

## Session control (Sprint 2)

- **Start / Pause / Resume / Stop** are local only, in `desktop-app/session/`. There is no network entry point. Pause closes the capture devices and clears the in-memory jitter buffer, so nothing further is yielded. Resume continues the same session id. Stop releases the devices, clears the buffer, and drops the session id.
- The jitter buffer is bounded (default 8 seconds of canonical PCM). It refuses writes while paused or stopped. Pause and Stop discard whatever it holds. It is not a meeting archive and is never written to disk.
- Phrase detection is `backend/stt/phrase_detector.py`. It returns a hit only when the configured phrase is present as whole words. A partial phrase, an empty configuration, or text inside a `[paused ...]` gap marker returns nothing. The detector keeps only a short tail so a phrase can span two text deltas, and it does not log that text. `desktop-app/wakeword/` is not a second matcher.
- The production phrase is still an open Legal/product item. Callers pass it in; the detector does not choose one and does not read the environment.

## Streaming and continuous STT (Sprint 3)

- While a session is running, mixed PCM is sent over TLS to the backend WebSocket. Auth is a scoped bearer token or mutual TLS. The listener defaults to `127.0.0.1` and refuses to serve without a certificate. Placing that listener on a private VNet is still deployment; this repo does not provision one.
- Pause stops audio on the wire and stops recognition. The transcript keeps a gap marker. Speech from the paused interval is not appended. Resume continues the same server session.
- A transport or recognition failure stops further audio and does not flush a backlog. Capture that dies while running still pauses the local session and clears the jitter buffer.
- Final utterances go into an in-memory transcript. A certain phrase freezes a slice through the end of the question. Recognition is not stopped to take that slice. Later meeting text stays on the live transcript. Uncertain detection does not freeze a slice.
- Azure Speech is the production recognizer and hears unpaused session audio. Unit tests use a fake SDK and do not call Azure. Consent must match that posture.
- The production trigger phrase is still an open Legal/product item.

## Still ahead of this code

- The assistant response is appended to the same session transcript, then spoken. Orchestration and TTS are not wired yet. The transcript can already store an assistant turn so a later slice includes it.
- Governed storage stores the running transcript (meeting text + assistant turns + gap markers). No raw audio field. Stop finalizes the session and clears memory after that write.

## Still in force

- Desktop-to-backend traffic is authenticated and TLS-only.
- Unity Catalog writes are board-tier ACL and must never include a raw audio field.
- Azure Speech, Anthropic, and Databricks credentials come from the secrets manager / environment, never from source.
- No speaker identification or diarization.
