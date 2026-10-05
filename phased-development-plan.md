# Board Knowledge Assistant — Phased Development Plan

Engineering execution plan, broken into ~1-2 week sprints. Assumes the desktop-app architecture (manual Start / Pause / Resume / Stop, no C# media SDK). Session behavior is defined in [`docs/session-model.md`](docs/session-model.md). Each sprint has a concrete exit gate; don't move to the next until it's met.

The older clip-only flow (send only the triggered utterance) is withdrawn. While running, transcription is continuous; the trigger phrase collects the transcript through the question for analysis; the response is written back into that transcript.

---

## Repo structure (proposed)

```
board-assistant/
├── desktop-app/              # Python desktop capture app
│   ├── capture/               # loopback + mic capture, mixing
│   ├── wakeword/               # trigger-phrase detection (transcript-side)
│   ├── transport/             # secure streaming client to backend
│   ├── playback/               # response audio playback
│   └── ui/                     # tray icon, Start / Pause / Resume / Stop
├── backend/
│   ├── orchestration/         # Claude Agent SDK engine
│   ├── tools/
│   │   ├── genie_tool/         # MCP wrapper for Genie (Phase 2)
│   │   └── external_tool/      # MCP wrapper for external agent (Phase 3)
│   ├── stt/                    # Azure Speech continuous STT
│   ├── tts/                    # Azure Speech TTS integration
│   └── storage/                # Unity Catalog write layer
├── infra/                      # IaC for VNet, Key Vault, endpoints
└── docs/                       # this plan, session model, security notes, consent
```

---

## Phase 1: Capture + Orchestration + Live Agent

### Sprint 1 — Desktop capture foundation
- Set up repo, Python project scaffolding, CI basics
- Implement WASAPI loopback capture (`pyaudiowpatch`)
- Implement microphone capture (`sounddevice`)
- Build the mixer combining both streams
- **Gate**: recorded test session shows both remote and in-room speech clearly audible in the mixed stream

### Sprint 2 — Session control + phrase trigger
- Session state machine: Start / Pause / Resume / Stop (local only)
- In-memory audio stream buffer (transient, no disk writes); pause must stop capture
- Trigger-phrase detector against incoming transcript text (exact phrase is an open Legal/product item)
- **Gate**: Pause stops mixed capture (nothing further is yielded); Resume continues the same session; Stop tears down. Phrase detector is certain/uncertain correctly on a synthetic transcript fixture

### Sprint 3 — Secure streaming transport + continuous STT
- Build the HTTPS/WebSocket streaming client in the desktop app, mutual TLS or scoped token auth
- Stand up the backend receiving endpoint (private VNet, signature/token verification)
- Stream mixed PCM only while running; Pause cuts the stream
- Wire Azure Speech **continuous** STT into an in-memory session transcript (meeting text + pause gap markers)
- **Gate**: unpaused speech appears in the transcript; speech spoken during Pause does not; on phrase, the transcript slice through the question is available without stopping STT

### Sprint 4 — Orchestration engine + stub tools
- Install and configure Claude Agent SDK
- Build stub internal and external MCP tools (fixed responses)
- Wire phrase → transcript slice through question → orchestration → stub tool → composed answer
- Append the answer to the session transcript as an assistant turn (STT still running)
- **Gate**: hardcoded test question produces a stub-based answer, and that answer is present in the session transcript while STT continues

### Sprint 5 — Response delivery
- Wire Azure Speech streaming TTS on the backend
- Build the desktop app's playback module
- Play the same text that was appended to the transcript
- **Gate**: full round trip on a single test machine: Start → speech → phrase + question → spoken answer that is also in the transcript → Pause omits a span → Resume → Stop

### Sprint 6 — Governed storage
- Provision Unity Catalog board schema (running transcript, question slice, answer, pause gaps), board-tier ACL
- Wire backend writes after each interaction and on Stop
- **Gate**: test sessions show the running transcript (including assistant turns) correctly scoped in Unity Catalog; no raw audio field; paused spans are gap markers, not speech

### Sprint 7 — Endpoint security hardening
- Code-sign the desktop app
- Restrict network egress at OS/firewall level to backend endpoint only
- Coordinate with IT to flag the app as a sanctioned EDR exception
- Confirm Start / Pause / Resume / Stop cannot be triggered remotely, local action only
- **Gate**: IT sign-off on the hardened build

### Sprint 8 — Consent, packaging, pilot
- Finalize consent notice and retention policy with Legal against the **session model** (unpaused session is transcribed), not the old clip-only description
- Package and distribute via IT's device management to the target laptop(s)
- Run 3-5 real/dry-run pilot sessions
- **Gate**: Phase 1 exit criteria met — reliable phrase detection, transcription continues across Q&A, Pause is honored, answers land in the transcript, clean audit logs, Legal sign-off, IT sign-off

---

## Phase 2: Genie Agent (internal data)

### Sprint 9 — Genie space + schema
- Provision Genie space in central workspace
- Structure board history (decisions, action items) into queryable tables
- Connect relevant internal BI dashboards
- **Gate**: Genie answers a set of realistic sample questions correctly via its own UI, before any integration

### Sprint 10 — Cross-workspace access + MCP wrapper
- Configure Unity Catalog ACLs for scoped cross-workspace read access
- Build the MCP wrapper around Genie's Conversation API
- Swap into orchestration engine, replacing the internal stub
- **Gate**: end-to-end test with real internal questions, answers correctly grounded and scoped, and written back into the session transcript

---

## Phase 3: External Intelligence Agent

### Sprint 11 — Standing feed + relevance tagging
- Set up news API ingestion, competitor/press sources
- Build SBU-relevance tagging logic
- **Gate**: feed produces correctly tagged, relevant items against a test set of sample topics per SBU

### Sprint 12 — Agent + MCP wrapper
- Configure the second Claude Agent SDK instance with the standing feed + built-in web search
- Build the MCP wrapper, swap into orchestration engine, replacing the external stub
- **Gate**: end-to-end test with both real agents connected, combined internal+external questions answered correctly and coherently, responses in the transcript

---

## After Sprint 12

Full pilot: 2-3 real board meetings, both agents live, human review of every answer before trusting it unsupervised. Use results to decide whether Phase 2 of the original proposal (proactive surfacing, individual attribution) is worth pursuing. Individual attribution remains out of scope unless that decision is explicit — this system still must not identify speakers.

## Notes on sequencing

- Sprints 1-8 (Phase 1) must complete in order, each builds on the last.
- Sprints 9-10 (Genie) and 11-12 (external agent) can run in parallel once Sprint 4's MCP tool interface contract is stable, they don't depend on each other.
- Legal/consent work should start no later than Sprint 1, not wait for the technical build to finish, and must track the session-model change (continuous unpaused transcription).
