# Board Knowledge Assistant — Modular Development Plan

Rebuilt around one rule: every segment gets built in isolation, behind a defined interface, with its own unit tests using mocked dependencies, before it's wired to anything else. Integration tests only run once the segments they connect are each independently proven. Nothing gets "tested together for the first time" as a surprise, integration is where you confirm two already-correct things talk to each other correctly, not where you discover bugs in either one.

Session behavior (Start / Pause / Resume / Stop, continuous transcript, phrase → analysis slice, response written back into the transcript) is defined in [`docs/session-model.md`](docs/session-model.md).

---

## Development methodology

**What counts as a "segment"**: the smallest unit with one clear responsibility and a defined interface, roughly a class or a small module, small enough to unit test completely with everything around it mocked. If you can't describe a segment's job in one sentence, it's not a segment, it's two.

**The loop, per segment:**
1. Define the interface first (function signatures, expected inputs/outputs, error cases), before implementation
2. Implement the segment
3. Write unit tests against that interface, with every external dependency mocked (no real network calls, no real audio hardware, no real API keys, ever, in a unit test)
4. Don't move to the next segment until this one's tests pass and cover its documented error cases, not just the happy path

**Integration tests sit above segments, not inside them.** Once two or more segments that need to work together are each independently unit-tested, write an integration test that wires the real implementations together (still against test/sandbox endpoints where external services are involved, never production credentials) and confirms the boundary between them behaves as both sides assumed.

**Test pyramid for this project**: many unit tests (fast, isolated, run on every commit), fewer integration tests (slower, run per-segment-group, cover real wiring), a handful of end-to-end tests (slowest, run per-phase-gate, cover the full user-facing flow).

**Tooling**: `pytest` for all tests, `pytest-asyncio` for the async code, `unittest.mock` / `pytest-mock` for mocking, a `conftest.py` per module providing standard fixtures (mock Azure Speech STT client, mock Azure Speech TTS client, mock Databricks/Genie client, mock Claude Agent SDK responses) so no test suite reinvents its own mocks. CI blocks merges on any failing test, and on any new segment shipped without corresponding unit tests, enforce this with a coverage gate, not just a review comment.

---

## Phase 1: Capture + Orchestration + Live Agent

### Subsystem A: Desktop audio capture

**Segment A1 — Loopback capture wrapper**
- Interface: `LoopbackCapture.start() -> AsyncIterator[bytes]`, `LoopbackCapture.stop()`
- Implement using `pyaudiowpatch`
- Unit test: mock the underlying WASAPI device, verify start/stop lifecycle, verify it yields chunks in the expected format, verify stop() actually releases the device handle

**Segment A2 — Microphone capture wrapper**
- Interface: same shape as A1, `MicCapture.start() -> AsyncIterator[bytes]`, `MicCapture.stop()`
- Unit test: same pattern, mocked device, lifecycle and format checks

**Segment A3 — Stream mixer**
- Interface: `AudioMixer.mix(stream_a: AsyncIterator[bytes], stream_b: AsyncIterator[bytes]) -> AsyncIterator[bytes]`
- Unit test: feed two synthetic mocked streams with known content, assert the mixed output preserves both without corrupting either (use simple, recognizable test tones or patterns, not real audio, for deterministic assertions)

**Integration test A**: wire A1 + A2 + A3 together against real (test environment) audio devices, confirm a real mixed capture session produces a coherent combined stream. This is the first point real hardware is involved.

---

### Subsystem B: Session control + phrase trigger

**Segment B1 — Session state machine**
- Interface: `SessionController.start()`, `pause()`, `resume()`, `stop()` → current `SessionState`
- Local actions only. Pause must stop capture; Resume must not start a new transcript; Stop must end the session
- Unit test: illegal transitions rejected; pause guarantees no further mixed chunks are yielded; stop releases devices

**Segment B2 — Transient stream buffer**
- Interface: `StreamBuffer.write(chunk: bytes)`, `StreamBuffer.clear()`
- In-memory only, bounded, for the live STT stream (jitter), not a "save the meeting" buffer
- Unit test: never exceeds max size; `clear()` on Pause/Stop leaves nothing retrievable; paused writes are refused

**Segment B3 — Trigger-phrase detector**
- Interface: `PhraseDetector.observe(transcript_delta: str) -> PhraseHit | None`
- Pure function over transcript text. Lives next to `SessionTranscript` on the backend (where STT text exists). Desktop `wakeword/` may mirror a hit for UI only
- Certain hit only when the configured phrase is present; uncertain / partial → `None` (fail closed for analysis)
- Unit test: table-driven synthetic transcripts (phrase present, absent, partial, during a pause gap marker)

**Integration test B**: wire B1–B3 to Subsystem A's mixed output (mocked STT deltas for B3), confirm Pause stops chunks, Resume continues the same session id, and a phrase in the transcript is detected without stopping the stream.

---

### Subsystem C: Secure streaming transport

**Segment C1 — Backend client**
- Interface: `BackendClient.start_session()`, `send_audio(chunk: bytes)`, `pause_session()`, `resume_session()`, `stop_session()`
- Handles auth (mutual TLS or scoped token), retries, timeouts
- Unit test: mock the HTTP/WebSocket layer entirely, verify correct auth headers, verify pause stops further `send_audio` calls, verify it never logs the audio payload, verify a transport error does not flush a backlog of meeting audio

**Segment C2 — Receiving endpoint (backend side)**
- Interface: authenticated streaming route that accepts session control + audio chunks
- Unit test: mock auth verification, reject unauthenticated requests, reject audio on a paused or unknown session, pass running-session chunks to STT (mocked)

**Integration test C**: run C1 against a real (test-environment) instance of C2, confirm TLS/auth works, pause actually stops the stream, and wrong credentials fail closed.

---

### Subsystem D: Continuous STT + orchestration

**Segment D1 — Azure Speech streaming wrapper**
- Interface: `SpeechToText.start_session() -> TranscriptStream`, `pause()`, `resume()`, `stop()`
- Continuous recognition into text deltas. Pause stops recognition; it does not discard the session transcript
- Unit test: mock the Azure Speech SDK client, verify pause/resume, verify error handling, verify no audio bytes are written to disk

**Segment D2 — Session transcript**
- Interface: `SessionTranscript.append_meeting(text)`, `append_pause_gap(start, end)`, `append_assistant(text)`, `slice_through_question(phrase_hit) -> str`
- In-memory only. Assistant turns are labeled as assistant, never as a person
- Unit test: paused speech is not appended (only the gap marker); assistant text is in the slice for a later question; `slice_through_question` stops at question-end, not at "now"

**Segment D3 — Question extractor**
- Interface: `extract_question(slice: str, phrase_hit: PhraseHit) -> Optional[str]`
- Returns the question following a certain phrase; `None` if it looks like noise
- Unit test: table-driven transcript samples, including "phrase with no question" → `None`

**Segment D4 — Orchestration engine wrapper**
- Interface: `Orchestrator.answer(transcript_slice: str, question: str) -> str`
- Wraps the Claude Agent SDK session. Input is the transcript through the question, not a lone clip
- Unit test: mock the SDK client entirely, verify MCP config, system prompt, and that the returned string is the answer to append

**Segment D5 — Stub internal tool**
**Segment D6 — Stub external tool**
- Both: trivial MCP servers returning fixed responses
- Unit test: verify each responds correctly to a well-formed MCP tool call

**Integration test D**: wire D1 → D2 → B3 → D3 → D4 (with D5/D6 as real stubs), confirm a synthetic running transcript plus a phrase produces an answer that D2 then holds as an assistant turn, while further meeting deltas still append.

---

### Subsystem E: Response delivery

**Segment E1 — TTS wrapper**
- Interface: `TextToSpeech.synthesize(text: str) -> AsyncIterator[bytes]`
- Wraps Azure Speech streaming TTS. Input must be the same text already appended to the transcript
- Unit test: mock the Azure Speech TTS client, verify correct request formatting and streaming chunk handling

**Segment E2 — Local playback**
- Interface: `AudioPlayer.play(stream: AsyncIterator[bytes])`
- Unit test: mock the audio output device, verify it consumes the full stream without dropping chunks

**Integration test E**: wire E1 + E2 against a real (test) Azure Speech TTS call and real local playback, confirm audible, correct output. A TTS failure after D2.append_assistant must not remove the assistant turn from the transcript.

---

### Subsystem F: Governed storage

**Segment F1 — Unity Catalog write client**
- Interface: `BoardStorage.log_interaction(session_id, transcript: str, question: str, answer: str, timestamp: datetime)` and `finalize_session(...)`
- `transcript` is the running session log (meeting text + assistant turns + gap markers)
- Unit test: mock the Databricks/Unity Catalog client, verify schema/table targeting, ACL-scoped credentials, and that no raw audio field exists in the write payload (assert the field is absent, not just untested)

**Integration test F**: run F1 against a real test-environment Unity Catalog schema, confirm writes land correctly, assistant turns are in the stored transcript, and an out-of-scope credential is denied.

---

### Phase 1 end-to-end test (after all subsystems A-F are integration-tested independently)

Wire every subsystem together for the first time as a complete system, in a test environment, using synthetic/non-sensitive test audio:
1. Start desktop app, confirm capture and streaming begin only on manual Start
2. Speak a non-trigger sentence, confirm it **is** transcribed, and confirm orchestration is **not** invoked
3. Speak the trigger phrase + a test question, confirm STT **continues**, the slice through the question is analyzed (stub tools), the answer is **in the transcript**, then synthesized and played
4. Pause, speak something, Resume; confirm the paused span is a gap marker, not speech
5. Confirm a governed log entry was written with the running transcript (including the assistant turn) and no raw audio field
6. Stop the app, confirm capture and streaming genuinely end

**Gate**: this full sequence passes reliably across multiple runs before moving to Phase 2.

---

## Phase 2: Genie Agent

**Segment G1 — Genie Conversation API wrapper**
- Interface: `GenieClient.query(question: str) -> QueryResult`
- Unit test: mock the Genie API client, verify request formatting, verify read-only enforcement is present in the request construction itself (test that no write-capable method exists on this class at all, not just that it isn't called)

**Segment G2 — MCP tool wrapper around G1**
- Unit test: verify it correctly exposes G1 as an MCP tool matching the interface contract from Subsystem D

**Integration test G**: swap G2 into Orchestrator (D4) in place of the D5 stub, against a real test-environment Genie space, confirm real internal questions produce grounded, correctly-scoped answers that are appended to the session transcript.

---

## Phase 3: External Intelligence Agent

**Segment H1 — Standing feed client** (news APIs, relevance tagging)
- Unit test: mock the feed sources, verify tagging logic against known sample items

**Segment H2 — MCP tool wrapper around H1 + built-in web search**
- Unit test: verify correct MCP exposure, verify the external agent's context never receives anything from the internal (Genie) tool, this boundary gets an explicit test, not just a code review note

**Integration test H**: swap H2 into Orchestrator in place of the D6 stub, confirm real external questions, and combined internal+external questions, produce correct, coherent answers that land in the transcript.

---

## Final integration: all subsystems together

Run the full Phase 1 end-to-end test again, now with G2 and H2 replacing the stubs. This is the only point in the whole plan where every real component runs together for the first time, and by this point each piece has already been proven correct on its own, so any failure here is specifically a wiring/boundary issue, not a hidden bug in an individual component, much faster to diagnose.

**Gate**: full pilot readiness, proceed to the 2-3 real board meeting pilot from here.
