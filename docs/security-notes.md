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

## Desktop window

- `python -m ui.gui` is a Qt Quick window over the same session wiring as the console. Start / Pause / Resume / Stop are buttons in that window only; there is no network or IPC entry point, and the QML loads nothing remote.
- The window never receives audio. It shows session state, listening time, whether an answer is playing, an error message built from the exception class (never its text), and the latest question and answer (below).

### Question and answer on screen (decision, 2026-10-26)

The project owner chose to show the question and answer in the window, with the question appearing live while it is spoken. Until then the window received no transcript text. Scope and safeguards:

- **Only text after the trigger phrase.** The backend sends the words that follow the phrase, the final question, and the answer. Nothing said before the phrase, and no other meeting text, is sent to the laptop. Paused speech is never captured, so it is never sent.
- **The cue never starts analysis.** The window reacts as soon as the phrase appears in Azure's in-progress (partial) results, so the room sees an immediate response. Analysis still runs only on a certain phrase in a final utterance. A cue the final text does not confirm is withdrawn (`trigger_cancelled`). Partial text never enters the transcript, storage or analysis.
- **Same channel, strictly checked.** Events travel on the existing authenticated TLS `/playback` socket, which stays receive-only. The desktop accepts only the defined event types and fields, with text capped at 5,000 characters; anything else is a protocol error. A failing window handler never stops playback, and a failing event send never affects the session.
- **Memory only on the laptop.** The text is held in memory for display, never logged and never written to disk. It stays on screen until the next question replaces it, and is cleared on Stop.
- Anyone who can see the laptop screen can read the latest question and answer. Position the screen with that in mind.
- If capture pauses itself after a device failure, or the link to the backend drops, the window shows "Audio interrupted" and offers only Stop. It does not offer Resume, so a failure is never presented as a pause the room chose.
- Closing the window during a session stops it first and lets an answer that is already playing finish.
- Dependency: `PySide6-Essentials` and `qasync` (the `gui` extra). The full `PySide6` package, which adds Qt WebEngine (Chromium), is deliberately not used.

## Governed storage (Sprint 6)

- The write contract is `backend/storage/board.py`: an interaction record per answered question (question, slice, answer, running transcript, timestamp) and a session record at Stop (start/stop times, question count, pause gap times, full transcript). There is no audio field; `payload_of` refuses any field that is not text, an integer, an aware timestamp or pause-gap times, so binary data cannot be written.
- `LiveSession` writes the interaction record after the answer is on the transcript, and the session record at Stop after in-flight answers finish. It then clears the transcript from memory. A storage failure logs the error class only; the answer stays on the transcript and memory is still cleared.
- Answers dropped during a pause are not stored. Paused speech is never captured, so it cannot reach storage.

### Decision: local-folder storage until Unity Catalog exists (2026-10-26)

The project owner chose to store records in a local folder for development and testing, because no Databricks workspace is available yet. This is a deliberate exception to "the only durable copy is Unity Catalog". Board-meeting text now sits in files on the backend machine. Safeguards in `backend/storage/local.py`:

- **Off by default.** Nothing is stored unless `LOCAL_STORAGE_DIR` is set, and it must be an absolute path.
- **Development and test only.** The backend refuses to start with local storage unless `ENVIRONMENT` is `development` or `test`; a blank or `production` value is refused.
- **Locked folder.** Before anything is written, the folder's Windows ACL is reset to the current user only (`icacls /inheritance:r /grant:r <user>:(OI)(CI)F`; mode `0700` elsewhere). If that fails, storage is refused and the backend does not start.
- **Whole files only.** Each record is written to a temporary file and renamed, so a crash cannot leave a partial record.
- **Never committed.** `storage-data/` is gitignored.

Known limits, accepted for development use:

- Files are not encrypted. Anyone who can act as that Windows user, or an administrator, can read them.
- The default folder is inside the project, which is in **OneDrive**, so OneDrive syncs these files to Microsoft's cloud under the user's account. Point `LOCAL_STORAGE_DIR` outside OneDrive to avoid that.
- There is no retention or deletion schedule; delete session folders by hand. Retention for real records is still open with Legal ([consent.md](consent.md)).
- Do not use this with real board content outside an approved test. Production storage remains Unity Catalog with board-tier ACL.

## Still ahead of this code
- Unity Catalog implementation of the same storage contract, with board-tier ACL and a write-only credential separate from the Genie read token.

## Still in force

- Desktop-to-backend traffic is authenticated and TLS-only.
- Unity Catalog writes are board-tier ACL and must never include a raw audio field.
- Azure Speech, Anthropic, and Databricks credentials come from the secrets manager / environment, never from source.
- No speaker identification or diarization.
