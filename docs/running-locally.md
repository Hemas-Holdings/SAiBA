# Running locally

How to run the backend and the desktop app together on one Windows machine for testing. Session behaviour is defined in [session-model.md](session-model.md); this file covers setup and commands only.

The two parts run in **separate terminals** and talk over a local TLS WebSocket on `127.0.0.1:8443`:

| Part | Folder | Command | What it does |
| --- | --- | --- | --- |
| Backend | `backend/` | `python -m orchestration` | Receives audio, transcribes (Azure Speech), answers (Claude), speaks answers (Azure TTS) |
| Desktop app | `desktop-app/` | `python -m ui.gui` (window) or `python -m ui` (console) | Local Start / Pause / Resume / Stop, captures and streams audio, plays answers |

Audio is never written to disk. Transcripts are written only if you turn on local storage (section 2, `LOCAL_STORAGE_DIR`); otherwise nothing is kept after a session ends.

---

## 1. Prerequisites

- Windows 10/11 (live speaker capture uses WASAPI loopback; on macOS/Linux use `--mic-only`)
- Python 3.11 or newer
- Headphones, so the microphone does not pick up the speakers
- Credentials for Azure Speech and Anthropic, and a shared bearer token for the backend

---

## 2. One-time setup

Run these from the repository root in PowerShell.

### Backend environment

```powershell
cd backend
python -m venv .venv-b
.\.venv-b\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv-b\Scripts\python.exe -m pip install -e ..\desktop-app
cd ..
```

The second install is needed because the backend's TLS tests use the desktop client.

### Desktop environment

```powershell
cd desktop-app
python -m venv .venv-d
.\.venv-d\Scripts\python.exe -m pip install -e ".[dev,windows,gui]"
cd ..
```

- `windows` adds speaker (loopback) capture.
- `gui` adds the desktop window (`PySide6-Essentials` and `qasync`). Do not install the full `PySide6` package: it bundles Qt WebEngine (Chromium), which this project deliberately avoids.

### Local TLS certificate

```powershell
cd backend
.\.venv-b\Scripts\python.exe -m tests.devcert ..\certs
cd ..
```

This writes `certs\cert.pem` and `certs\key.pem` for `127.0.0.1`. They are for local testing only and are gitignored.

### Settings file

```powershell
copy .env.example .env
```

Then fill in `.env`. Use **absolute paths**, because each part runs from its own folder:

```ini
# Desktop -> backend link
BACKEND_BASE_URL=wss://127.0.0.1:8443
BACKEND_AUTH_MODE=token
BACKEND_AUTH_TOKEN=<any long random string; both parts use the same value>
TLS_CA_CERT_PATH=C:\path\to\saiba\certs\cert.pem

# Backend listener
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8443
TLS_CERT_PATH=C:\path\to\saiba\certs\cert.pem
TLS_KEY_PATH=C:\path\to\saiba\certs\key.pem

# Speech and trigger phrase
AZURE_SPEECH_KEY=<key>
AZURE_SPEECH_REGION=<region, e.g. eastus>
TRIGGER_PHRASE=<the phrase that starts a question>

# Orchestration
ANTHROPIC_API_KEY=<key, on one line>
ANTHROPIC_ORCHESTRATION_MODEL=

# External tool: live web search on trusted sites (optional; off by default)
EXTERNAL_SEARCH_ENABLED=true
EXTERNAL_SEARCH_ALLOWED_DOMAINS=bloomberg.com,cnbc.com,cse.lk,cbsl.gov.lk,economynext.com,ft.lk,dailymirror.lk,sundaytimes.lk,newsfirst.lk,imf.org,worldbank.org
EXTERNAL_SEARCH_MAX_USES=5

# Transcript storage (optional; development and test only)
ENVIRONMENT=development
LOCAL_STORAGE_DIR=C:\path\to\saiba\storage-data
```

Notes:

- `BACKEND_AUTH_MODE` must be `token`; the desktop app does not support `mtls` yet.
- Leave `ANTHROPIC_ORCHESTRATION_MODEL` empty to use the default (`claude-opus-5-5`). An invalid model id does not crash anything; questions just get no answer.
- `LOCAL_STORAGE_DIR` turns on transcript storage. Leave it empty to keep nothing. It must be an absolute path, and the backend refuses to start with it unless `ENVIRONMENT` is `development` or `test`. On first start the folder is created and locked to your Windows account.
- Never commit `.env`. It is gitignored.

---

## 3. Start the backend (terminal 1)

```powershell
cd backend
.\.venv-b\Scripts\python.exe -m orchestration --env-file ..\.env --print-transcript
```

Wait for:

```
INFO orchestration: backend listening port=8443
```

`--print-transcript` prints each session's transcript to this console when the session ends. It is for testing only; leave it off otherwise. Stop the backend with `Ctrl+C`.

---

## 4. Start the desktop app (terminal 2)

### Window (recommended)

```powershell
cd desktop-app
.\.venv-d\Scripts\python.exe -m ui.gui --env-file ..\.env
```

Use the **Start session**, **Pause** / **Resume** and **Stop** buttons. Closing the window during a session stops it first and lets an answer that is playing finish.

### Console (alternative)

```powershell
cd desktop-app
.\.venv-d\Scripts\python.exe -m ui --env-file ..\.env
```

Type a letter and press Enter: `s` start, `p` pause, `r` resume, `q` stop and quit.

Add `--mic-only` to either command to capture the microphone only (no speaker capture), for example off Windows.

Run only one desktop app at a time; two would capture the same microphone.

---

## 5. Test a round trip

1. **Start.** Say a normal sentence. It is transcribed; no answer is given.
2. **Ask.** Say the trigger phrase, then a question. The answer is spoken once (it does not trigger itself) and the window shows **Answering**.
3. **Pause.** Say something. It must not appear in the transcript.
4. **Resume.** Ask another question.
5. **Stop** while the answer is playing. The answer finishes before the session closes.

Then check the transcript printed in terminal 1. It should contain the meeting text, `Assistant:` lines for each answer, and a `[paused HH:MM–HH:MM]` marker with none of the paused speech.

---

## 6. Find stored transcripts

With `LOCAL_STORAGE_DIR` set, each session gets its own folder, named by its session id:

```
storage-data\
  3f2b9c1e-...\
    interaction-0001.json   one per answered question: question, slice, answer, transcript so far
    interaction-0002.json
    session.json            written at Stop: start/stop times, pause times, full transcript
```

The files are readable JSON and contain no audio. Open them with any text editor, or list sessions with:

```powershell
Get-ChildItem storage-data | Sort-Object LastWriteTime
```

To delete a session's records, delete its folder. There is no automatic clean-up.

Notes:

- The folder is locked to your Windows account. Other accounts on this machine cannot read it, but administrators can.
- The files are not encrypted.
- The default folder is inside OneDrive, so OneDrive uploads these files to Microsoft's cloud. To keep transcripts on this machine only, set `LOCAL_STORAGE_DIR` to a folder outside OneDrive, such as `C:\Users\<you>\BoardAssistantData`.
- This is for development and testing. Production storage will be Unity Catalog.

---

## 7. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Backend exits with `missing settings: ...` | A required setting in `.env` is empty; the message names it |
| Window shows "the assistant backend did not respond" | Backend is not running, or `BACKEND_AUTH_TOKEN` differs between the two parts |
| Desktop exits with "supports BACKEND_AUTH_MODE=token only" | Set `BACKEND_AUTH_MODE=token` |
| TLS / certificate error on connect | `TLS_CA_CERT_PATH` is not an absolute path to `certs\cert.pem`, or the cert was regenerated while the backend was running |
| Trigger phrase heard but no answer | Check terminal 1: `orchestration failed` or `result failed` means a bad API key or model id; `phrase had no question` means nothing followed the phrase |
| Window shows **Audio interrupted** | A capture device failed or the link dropped. Press Stop, then start a new session |
| Answer is transcribed back as meeting speech | Use headphones or lower the speaker volume |
| Backend exits with "local storage is allowed only when ENVIRONMENT is development or test" | Set `ENVIRONMENT=development`, or empty `LOCAL_STORAGE_DIR` to store nothing |
| Backend exits with "could not lock the storage folder" | The folder's permissions could not be set; choose a folder you own, outside protected locations |
| `Activate.ps1` is blocked | Not needed: the commands above call the venv's `python.exe` directly |

---

## 8. Run the tests

No credentials or devices are needed; external services are faked.

```powershell
cd backend
.\.venv-b\Scripts\python.exe -m pytest tests -m "not integration"

cd ..\desktop-app
.\.venv-d\Scripts\python.exe -m pytest tests -m "not integration"
```

Tests marked `integration` call real Azure / Anthropic services or open real audio devices. They are opt-in (`RUN_LIVE_AUDIO=1` for devices) and never run in CI.
