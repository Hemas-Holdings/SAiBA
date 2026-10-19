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

## Orchestration (Sprint 4)

- A frozen slice and the question after the phrase are sent to the Anthropic API through the Claude Agent SDK. That is the only data that leaves the backend for orchestration. Raw audio is never sent.
- The SDK runs locked down: built-in Claude Code tools (files, shell, web) are off, only the internal and external MCP tools are allowed, filesystem settings and other MCP servers are not loaded, and session persistence is off so the CLI does not write the conversation to disk.
- The system prompt treats the transcript as context, not instructions, to limit injection from what is said in the room.
- Any SDK failure gives an empty answer, so nothing is appended. Logs record only the error class and result subtype, never the question, slice or answer.
- Answering runs in the background. Recognition is not paused or awaited. An answer that returns while the session is paused is dropped, because the transcript refuses turns during a pause and nothing is delivered that is not in the transcript.
- Sprint 4 tools are stubs with fixed responses. They see only the question.

## Spoken delivery (Sprint 5)

- Answer text that is already on the transcript is sent to Azure Speech TTS (same Speech resource as STT). Azure is configured with no audio output, so nothing plays on the server, and the PCM stays in memory.
- Answer audio goes to the desktop on a separate playback WebSocket at `/playback`, on the same TLS listener and with the same bearer-token check as capture. The client sends one subscribe frame for a session id; it is accepted only for a session whose capture has started and has not finished draining, and only one playback socket per session. Session ids are random UUIDs. Any further client frame closes the socket. Unknown paths get 404.
- The capture client stays upload-only and fail-closed. A playback failure never affects capture, and a capture failure does not stop an answer that is already playing.
- While an answer plays, the desktop sends silence instead of the mix, so the answer is not sent to Azure STT as meeting speech or re-triggered.
- A capture socket that closes without Stop now stops its session, so recognition does not keep running.
- Neither side writes answer audio or transcript text to disk. The backend prints the transcript to its console only with the gate flag `--print-transcript`.
- Each capture connection gets its own transcript, recognizer and detector (`ReceiveServer(sink_factory=...)`), so sessions never share a transcript.
- Known gap: the entry points support bearer-token mode only. In mutual-TLS mode the receiver still requires a bearer token, which the desktop does not send.

## Still ahead of this code
- Governed storage stores the running transcript (meeting text + assistant turns + gap markers). No raw audio field. Stop finalizes the session and clears memory after that write.

## Still in force

- Desktop-to-backend traffic is authenticated and TLS-only.
- Unity Catalog writes are board-tier ACL and must never include a raw audio field.
- Azure Speech, Anthropic, and Databricks credentials come from the secrets manager / environment, never from source.
- No speaker identification or diarization.
