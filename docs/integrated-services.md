# Integrated services

Catalog of every library and vendor this system uses or is planned to use, what data it touches, and which environment variables it needs. Credentials never go in source; copy [`.env.example`](../.env.example) to `.env` locally, or load the same names from Azure Key Vault in deployed environments.

Sprint 1 capture and Sprint 2 session control are local-only. Sprint 3 streams mixed PCM over TLS to the backend; Azure Speech is the production recognizer, and unit tests do not call it. Other cloud services below are wired in later sprints. Their variables are listed now so local setup and Key Vault provisioning can stay ahead of the code.

## How to read this file

| Status | Meaning |
| --- | --- |
| **Active** | Used by current code |
| **Planned** | Named in the phased plan; not implemented yet |
| **Vendor TBD** | Required by the plan, specific product not chosen. Do not add an SDK until vendor-risk analysis covers it |

Internal (Genie) and external (news/web) agents must never share context. Genie is read-only at the code level. Raw audio is never written to disk. While a session is running (not paused), mixed PCM is streamed over authenticated TLS for continuous STT; analysis receives transcript text, not a meeting-audio dump. See [session-model.md](session-model.md).

---

## Local capture (no cloud credentials)

### WASAPI loopback — `pyaudiowpatch`

| | |
| --- | --- |
| **Status** | Active (Windows). Tests mock this on macOS/Linux |
| **Package** | `desktop-app/capture/loopback.py` |
| **Sprint** | 1 |
| **Data** | In-memory PCM from the system render device (remote/meeting audio). Never persisted |
| **Credentials** | None |

### Microphone — `sounddevice`

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `desktop-app/capture/microphone.py` |
| **Sprint** | 1 |
| **Data** | In-memory PCM from the default input device (in-room speech). Never persisted |
| **Credentials** | None |

### Mixer / listen-through — `numpy`, local playback

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `desktop-app/capture/mixer.py`, `desktop-app/capture/listen.py` |
| **Sprint** | 1 |
| **Data** | Combined PCM in memory. Listen-through plays it locally and discards it |
| **Credentials** | None |

Optional local flag: `RUN_LIVE_AUDIO=1` opts into tests that open real devices. CI never sets this.

---

## Desktop app services

### Desktop window — `PySide6-Essentials`, `qasync`

| | |
| --- | --- |
| **Status** | Active (optional `gui` extra) |
| **Package** | `desktop-app/ui/gui.py`, `desktop-app/ui/qml/Main.qml`, logic in `desktop-app/ui/controls.py` |
| **Data** | Local session state, listening time, whether an answer is playing, an error class name, and the latest question and answer (text after the trigger phrase only, in memory, cleared on Stop). No audio, no other meeting text |
| **Network** | None. Qt Quick renders locally; the QML loads no remote resources. Buttons are the only input |
| **Credentials** | None |

`PySide6-Essentials` only (Qt Core, GUI, QML, Quick). The full `PySide6` package is not used because it adds Qt WebEngine, a bundled Chromium. Qt has no usage telemetry. `qasync` runs the existing asyncio session code on the Qt event loop.

### Session control — local Start / Pause / Resume / Stop

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `desktop-app/session/` |
| **Sprint** | 2 |
| **Data** | Mixed PCM in a bounded in-memory buffer while running. Pause and Stop discard it. Same session id across Resume. No network control |
| **Credentials** | None |

`StreamBuffer` takes a duration in its constructor. The default is 8 seconds of canonical PCM, which is what `STREAM_BUFFER_SECONDS` names. This sprint does not read the environment inside the buffer.

### Trigger phrase (transcript-side)

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `backend/stt/phrase_detector.py`. `desktop-app/wakeword/` must not add a second matcher |
| **Sprint** | 2 |
| **Data** | Observes transcript text deltas. Certain phrase → a hit for a later analysis step; uncertain, partial, empty, or inside a pause gap marker → no hit. No speaker identity. Does not log the delta |
| **Network** | None for detection itself |

A separate audio wake-word vendor (e.g. Picovoice) is **not** the default now that STT is continuous. Revisit only if text-side detection fails the Sprint 2 gate. The detector does not read the environment and does not choose the phrase. Callers pass `TRIGGER_PHRASE` in. Wording is still open with Legal/product; an empty phrase never hits. Continuous STT, which will produce the deltas, is Sprint 3.

| Variable | Required | Purpose |
| --- | --- | --- |
| `TRIGGER_PHRASE` | Yes | Phrase passed into `PhraseDetector` (wording still open with Legal/product) |
| `STREAM_BUFFER_SECONDS` | No | Bounded in-memory audio jitter buffer for the live stream, default `8` |

### Backend transport (desktop client)

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `desktop-app/transport/` client, `backend/orchestration/receive.py` server |
| **Sprint** | 3 |
| **Data** | Session control (start/pause/resume/stop) plus mixed PCM **while running**. Pause cuts the stream. A send failure drops unsent audio. Never logs the payload |
| **Auth** | Mutual TLS **or** a scoped token — not a shared static key |
| **Library** | `websockets` carries the TLS socket. It is not a second destination for meeting audio |

Use one auth mode. Leave the unused variables empty. The client and server take these values as constructor arguments; they do not read the environment themselves. The server binds to `BACKEND_BIND_HOST` (default `127.0.0.1`) and requires TLS. A private VNet is still a deployment step.

| Variable | Required | Purpose |
| --- | --- | --- |
| `BACKEND_BASE_URL` | Yes | `https://` receiving endpoint |
| `BACKEND_AUTH_MODE` | Yes | `mtls` or `token` |
| `BACKEND_AUTH_TOKEN` | If `token` | Short-lived scoped token |
| `TLS_CA_CERT_PATH` | If `mtls` | CA that signed the backend cert |
| `TLS_CLIENT_CERT_PATH` | If `mtls` | Desktop client certificate |
| `TLS_CLIENT_KEY_PATH` | If `mtls` | Desktop client private key |

---

## Backend speech

### Azure Speech — continuous STT

| | |
| --- | --- |
| **Status** | Active |
| **Package** | `backend/stt/speech.py`, `backend/stt/transcript.py` |
| **Sprint** | 3 |
| **Data** | Unpaused session audio in, running transcript out (meeting text, pause gaps, frozen question slice). Pause stops recognition. Audio is not written to disk. Transcript is not logged at INFO or below |
| **Region** | Prefer a private / approved Azure region; all traffic TLS |
| **Posture** | Azure hears unpaused session audio, not only the question. See session-model.md. Tests inject a fake SDK and do not call Azure |

Credentials are constructor arguments on `AzureSpeechAdapter`. The module does not read the environment.

### Azure Speech — streaming TTS

| | |
| --- | --- |
| **Status** | Implemented (Sprint 5) |
| **Package** | `backend/tts/` (`speech.py`); playback in `backend/orchestration/playback.py` and `desktop-app/playback/` |
| **Sprint** | 5 |
| **Data** | Answer text in (already on the transcript), raw 16 kHz mono PCM out, streamed to the desktop playback socket. Audio is not persisted |

STT and TTS share one Speech resource. Credentials are constructor arguments on `AzureSpeechSynthesisAdapter`; only `python -m orchestration` reads the environment.

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_SPEECH_KEY` | Yes | Speech resource key |
| `AZURE_SPEECH_REGION` | Yes | e.g. `eastus` |
| `AZURE_SPEECH_ENDPOINT` | No | Custom / private endpoint if not the regional default |
| `AZURE_SPEECH_LANGUAGE` | No | Recognition locale, default `en-US` |
| `AZURE_SPEECH_TTS_VOICE` | No | Neural voice name, e.g. `en-US-JennyNeural` |

---

## Orchestration

### Anthropic Claude Agent SDK (orchestrator)

| | |
| --- | --- |
| **Status** | Implemented (Sprint 4, stub tools) |
| **Package** | `backend/orchestration/` (`orchestrator.py`, `question.py`), `claude-agent-sdk` |
| **Sprint** | 4 |
| **Data** | Transcript slice through the question, plus the extracted question. Routes to internal and/or external MCP tools. Does not merge those tools' contexts. Answer is appended to the session transcript |
| **Network** | Anthropic API over TLS, through the Claude Code CLI bundled with the SDK |

The Sprint 3 receiver also lives in this package. That SDK instance stays the router when real tools replace the stubs. It runs with built-in Claude Code tools off, only the two MCP tools allowed, no filesystem settings, no other MCP servers, and no session persistence.

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `ANTHROPIC_ORCHESTRATION_MODEL` | No | Approved model id for the router agent. Defaults to `claude-opus-5-5` |

### MCP tool contract (Sprint 4)

Every internal and external tool implements `backend/tools/contract.py`, so a stub is replaced without changing the orchestrator:

| | |
| --- | --- |
| **Server** | One in-process MCP server per tool family: `internal` (`tools/genie_tool/`), `external` (`tools/external_tool/`) |
| **Tool** | `internal.query_internal_data`, `external.search_external` (allowed as `mcp__<server>__<tool>`) |
| **Input** | `{"question": string}`, required, no other fields |
| **Result** | MCP content with one text block, prefixed `[source: <label>]` |
| **Failure** | Same shape with `"is_error": true`. Tools do not raise and do not log question or result text |

The Sprint 4 stubs return fixed responses. Phase 2 (Genie) and Phase 3 (external agent) keep the server names, tool names and shapes.

### Anthropic Claude Agent SDK (external intelligence)

| | |
| --- | --- |
| **Status** | Planned |
| **Package** | `backend/tools/external_tool/` |
| **Sprint** | 12 |
| **Data** | Public/news context only. Must never receive Genie results |
| **Network** | Anthropic API plus web search / standing feed |

A **second** agent instance, not a second set of tools on the orchestrator.

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_EXTERNAL_MODEL` | Yes (Phase 3) | Approved model id for the external agent |

`ANTHROPIC_API_KEY` is shared unless security later requires a dedicated key.

---

## Internal data (read-only)

### Databricks Genie Conversation API

| | |
| --- | --- |
| **Status** | Planned |
| **Package** | `backend/tools/genie_tool/` |
| **Sprint** | 9–10 |
| **Data** | Natural-language questions against board history and internal BI. Wrapper must not expose write/update/delete SQL |
| **Auth** | Token scoped to Genie read only — not the storage write token |

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABRICKS_HOST` | Yes | Workspace URL, `https://...azuredatabricks.net` |
| `DATABRICKS_GENIE_TOKEN` | Yes | Read-scoped PAT or OAuth token |
| `DATABRICKS_GENIE_SPACE_ID` | Yes | Genie space id |
| `DATABRICKS_WAREHOUSE_ID` | Yes | SQL warehouse Genie is attached to |

---

## Governed storage (write path)

The contract is `backend/storage/board.py` (Sprint 6): an interaction record per answered question and a session record at Stop. No audio field. Implementations: a local folder for development and test (active), and Unity Catalog for production (planned).

### Local folder (development and test only)

| | |
| --- | --- |
| **Status** | Active (Sprint 6) |
| **Package** | `backend/storage/local.py` |
| **Data** | JSON files per session: `interaction-0001.json`, ..., `session.json`. Transcript text, questions, slices, answers, timestamps, pause gap times. **No audio** |
| **Access** | Folder locked to the current user before first write; refused if locking fails |
| **Limits** | Not encrypted. Refused unless `ENVIRONMENT` is `development` or `test`. A folder inside OneDrive is synced to Microsoft's cloud. See security-notes.md |

| Variable | Required | Purpose |
| --- | --- | --- |
| `LOCAL_STORAGE_DIR` | No | Absolute path of the storage folder. Empty means nothing is stored |
| `ENVIRONMENT` | If storage is set | Must be `development` or `test` for local storage |

### Databricks Unity Catalog

| | |
| --- | --- |
| **Status** | Planned |
| **Package** | `backend/storage/` |
| **Sprint** | 6 |
| **Data** | Running session transcript (meeting text + assistant turns + pause gap markers), question slice, answer, timestamp. **No raw audio field** |
| **ACL** | Board-tier; out-of-scope principals must be denied |
| **Auth** | Separate write-scoped token from the Genie read token |

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABRICKS_STORAGE_TOKEN` | Yes | Write-scoped token for the board schema only |
| `DATABRICKS_CATALOG` | Yes | Unity Catalog name |
| `DATABRICKS_SCHEMA` | Yes | Board schema |
| `DATABRICKS_INTERACTIONS_TABLE` | Yes | Table for interaction logs |

---

## External intelligence feed

### Standing news / press feed

| | |
| --- | --- |
| **Status** | Vendor TBD |
| **Package** | `backend/tools/external_tool/` |
| **Sprint** | 11 |
| **Data** | Public news and competitor/press items, SBU-relevance tagged. Never mixed into Genie context |

Do not add a news-API SDK until vendor-risk analysis is updated. Placeholders below exist so Key Vault structure is ready.

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEWS_API_KEY` | Phase 3 | Key for the approved feed vendor |
| `NEWS_API_BASE_URL` | Phase 3 | Vendor HTTPS base URL |

Claude built-in web search (Sprint 12) uses the Anthropic key; it does not need a separate search-API key unless a distinct vendor is approved.

---

## Platform / secrets

### Azure Key Vault

| | |
| --- | --- |
| **Status** | Planned (`infra/`) |
| **Sprint** | 3+ (as backend is deployed) |
| **Role** | Production secrets manager. Local `.env` is development only |

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_KEY_VAULT_URI` | Production | Vault URI, `https://<name>.vault.azure.net/` |
| `AZURE_TENANT_ID` | If service principal | Entra tenant |
| `AZURE_CLIENT_ID` | If service principal | App registration id |
| `AZURE_CLIENT_SECRET` | If service principal | Prefer managed identity in Azure instead |

### Backend process / TLS terminator

| Variable | Required | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | Yes | `development`, `test`, or `production` |
| `LOG_LEVEL` | No | Default `INFO`. Must never include audio bytes or transcript text |
| `BACKEND_BIND_HOST` | No | Default `127.0.0.1` locally |
| `BACKEND_BIND_PORT` | No | Default `8443` |
| `TLS_CERT_PATH` | Production | Backend server certificate |
| `TLS_KEY_PATH` | Production | Backend server private key |
| `TLS_CLIENT_CA_PATH` | If mTLS | CA used to verify desktop client certs |

---

## Variable checklist by sprint

| Sprint | What starts needing credentials |
| --- | --- |
| 1 | None (`RUN_LIVE_AUDIO` only for optional local hardware tests) |
| 2 | `TRIGGER_PHRASE`, `STREAM_BUFFER_SECONDS` |
| 3 | `BACKEND_*`, TLS paths, `AZURE_SPEECH_*` (continuous STT) |
| 4 | `ANTHROPIC_API_KEY` (`ANTHROPIC_ORCHESTRATION_MODEL` optional) |
| 5 | `AZURE_SPEECH_TTS_VOICE` (same Speech resource as STT) |
| 6 | `LOCAL_STORAGE_DIR` + `ENVIRONMENT` (dev/test local store); later `DATABRICKS_HOST`, `DATABRICKS_STORAGE_*`, catalog/schema/table |
| 9–10 | `DATABRICKS_GENIE_*`, `DATABRICKS_WAREHOUSE_ID` |
| 11–12 | `NEWS_API_*` (after vendor approval), `ANTHROPIC_EXTERNAL_MODEL` |

---

## Rules that apply to every service

- Never commit real values. `.env` is gitignored; `.env.example` stays empty.
- Unit tests mock these clients. CI must not hold production keys or call real APIs.
- Integration tests use a dedicated test environment and synthetic data only.
- A new vendor or SDK is a security-posture change; it is not a drop-in dependency.
