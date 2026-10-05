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

## Still ahead of this code

- While running, mixed audio is streamed over authenticated TLS to the private backend for **continuous STT**. Transcription does not pause for a question or an answer. Streaming and STT are not wired yet.
- This expands what Azure Speech and the backend hear from "triggered question clip" to **unpaused session audio**. Consent must match that fact.
- On a **certain** trigger phrase, analysis receives the **transcript text through the question**, not a raw-audio dump of the meeting. Uncertain detection → no analysis.
- The assistant **response is appended to the same session transcript**, then spoken. Governed storage stores that running transcript (meeting text + assistant turns + gap markers). No raw audio field. Paused speech must not appear in the transcript (gap marker only). Stop finalizes the session and clears memory after the governed write.
- Transport or STT failure fails closed: stop sending audio; do not flush a buffer of meeting audio. Capture that dies while a session is running already pauses the session and clears the buffer.

## Still in force

- Desktop-to-backend traffic is authenticated and TLS-only.
- Unity Catalog writes are board-tier ACL and must never include a raw audio field.
- Azure Speech, Anthropic, and Databricks credentials come from the secrets manager / environment, never from source.
- No speaker identification or diarization.
