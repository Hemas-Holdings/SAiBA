# Session model

Canonical spec for Start, Pause, Resume, Stop, continuous transcription, trigger, and how the assistant reply joins the transcript. Other plans defer to this file when they conflict with the older clip-only flow.

Sprint 1 is still local mixed capture only. This model is implemented from Sprint 2 onward.

## Why this replaced the clip-only flow

The original plan sent **only the triggered utterance** to the backend. That is withdrawn.

While a session is running and not paused:

1. Transcription **does not stop** for a question or an answer.
2. When the trigger **phrase** is heard, the backend collects the **session transcript through the end of that question** and sends **that text** (not a raw-audio dump of the meeting) for analysis.
3. The assistant **response is appended to the same transcript**, then spoken. Later questions therefore see prior answers as part of the meeting record.

This is a security-posture change. Unpaused session audio is transcribed for the life of the session. Legal must re-approve consent and retention against this file, not against the old “question clip only” description.

---

## Session states

Local action only. Start, Pause, Resume, and Stop must not be invocable over the network.

```
idle ──Start──► running ──Pause──► paused ──Resume──► running
                   │                  │
                   └──── Stop ────────┴──► idle  (session ended)
```

| Action | Capture | Stream to backend | STT | Phrase / analysis | Session transcript |
| --- | --- | --- | --- | --- | --- |
| **Start** | On | On | On | Armed | New empty transcript |
| **Pause** | Off | Off | Off | Disarmed | Kept; nothing new is appended except an explicit gap marker |
| **Resume** | On | On | On | Armed | Same transcript continues after the gap marker |
| **Stop** | Off | Off | Off | Disarmed | Finalized to governed storage, then cleared from memory |

Pause is not Stop. Pause is how the room keeps a side discussion, break, or sensitive exchange **out of the transcript** without ending the session.

Fail closed on transport or STT errors: treat like Pause (stop sending audio), surface the error, do **not** flush buffered meeting audio “just in case.”

---

## Running transcript

Held **in memory on the backend** for the life of the session. Never written to disk on the desktop. Never logged at INFO or below. The only durable copy is the governed storage write path: Unity Catalog in production, or, until a workspace exists, a locked local folder in development and test (see [Governed storage](#governed-storage-sprint-6) below).

The transcript is a single ordered log of:

1. **Meeting text** produced by continuous STT while the session is running (not paused). No speaker labels, no diarization, no voice enrollment.
2. **Assistant turns**, explicitly marked as the assistant (not as a person in the room).
3. **Pause gap markers**, so omitted time is auditable rather than silently missing. Example: `[paused 14:02–14:11]`.

Asking a question does not freeze STT. Meeting speech that happens while the model is working continues to append as meeting text. The analysis **slice** is frozen at question-end (below); the live transcript keeps moving.

---

## Trigger phrase → analysis slice

Detection runs **on the backend**, against the session transcript (where the text lives). The desktop `wakeword/` package may mirror a hit for UI only. It must not be a second, more-permissive trigger.

When the phrase is recognized with certainty:

1. Wait until the question that follows the phrase ends (end of utterance / short silence). If the phrase is uncertain, **do nothing** for analysis. STT may continue.
2. Build the analysis slice: session transcript from Start **through the end of that question**, excluding paused intervals (gap markers may remain so the model can see that time was omitted).
3. Send **that text** to the orchestrator. Do not send raw audio of the whole meeting as the analysis payload.
4. Do not wait for the answer before continuing STT.

**Unfinished questions.** People pause mid-question and recognition splits the sentence. If the utterance holding the phrase does not end in "?", the question is held for a short grace period (1.5 s). If speech resumes in that time (seen in in-progress recognition), the next final utterance joins the question; otherwise it is answered as it stands. A held question is never closed during a pause; the wait restarts on Resume, and a question still held at Stop is answered. In-progress text is used only to see that speech resumed; it never enters the slice.

The slice for a later question includes earlier assistant turns, because those turns are already in the session transcript.

---

## Response is part of the transcript

After orchestration returns an answer:

1. Append it to the session transcript as an assistant turn (text), in order, after the question it answers.
2. Synthesize and play that same text (Sprint 5). Playback is not a second source of truth; the transcript text is.
3. Persist via the governed write path: the **running transcript** (meeting text + assistant turns + gap markers), plus the question slice and answer for that interaction. **No raw audio field.**

If TTS fails after the text was appended, the transcript still contains the answer. Do not drop the text because playback failed.

Orchestration runs in the background (Sprint 4). Meeting text keeps landing while an answer is pending, so the assistant turn is appended where the answer arrives, after any meeting text spoken while it was being prepared. An answer that arrives during a pause is dropped rather than appended inside the gap. A phrase with nothing after it but filler is not a question and does not call the orchestrator.

### Spoken delivery (Sprint 5)

- The answer is spoken only after it is in the transcript. The audio comes back on a **separate, receive-only playback socket** (`/playback`), not the capture socket. The desktop subscribes once with its session id and then only receives.
- The same socket carries **window events** so the room can see the assistant respond: a cue as soon as the trigger phrase is heard (from in-progress recognition), the question live as it is spoken, the final question, and the answer text. Only text after the trigger phrase is sent; no other meeting text reaches the laptop. The cue is display only: analysis still runs only on a certain phrase in a final utterance, and an unconfirmed cue is withdrawn. The window keeps the latest question and answer in memory until the next question and clears them on Stop. See security-notes.md.
- **Pause and Stop do not cut an answer that is already playing.** They act on capture only. Stop ends recognition at once. Answers still being prepared at Stop finish, are appended, and are spoken; then the backend ends playback for that session.
- **Capture is muted while an answer plays**, plus a short tail. The desktop sends silence instead of the mix, because loopback records the laptop speaker and the answer would otherwise be transcribed as meeting speech and could set off the trigger. Anything said over the answer is therefore not transcribed. This is not a pause: no gap marker is written.
- Answers are spoken one at a time, in order. A TTS failure is reported to the desktop; the transcript keeps the answer.
- If the capture socket drops without Stop, the backend stops that session so nothing keeps listening.

### Governed storage (Sprint 6)

- **After each answered question**, one interaction record is written: the question, the slice it was asked against, the answer, the running transcript at that moment, and a timestamp.
- **At Stop**, once every answer still being prepared has finished, one session record is written: start and stop times, the number of questions, the pause gaps (start and end times only), and the full running transcript. The transcript is then cleared from memory. A session with no questions is still recorded.
- Records never contain audio. The contract (`backend/storage/board.py`) has no audio field and refuses binary data in any field.
- An answer dropped because it arrived during a pause is not stored, just as it is not on the transcript.
- A storage failure is reported (error class only) and never removes the answer from the transcript or stops the session.
- **Where records go**: Unity Catalog in production (not yet built). In development and test only, `LOCAL_STORAGE_DIR` points at a local folder; see [security-notes.md](security-notes.md) for that decision and its safeguards. With neither set, nothing is stored.

---

## Audio and STT placement

**Working default (same STT vendor as the original plan):** while running, mixed PCM is streamed over authenticated TLS to the private backend. The backend runs **continuous Azure Speech STT** into the in-memory session transcript. Pause cuts the stream. Audio is still never written to disk.

That default means Azure Speech (and the backend) hear **unpaused session audio**, not only the question. That is the cost of a continuous meeting transcript with no new STT vendor.

**More conservative alternative (not the default; would need a decision):** on-device / embedded STT so meeting audio never leaves the laptop; only the analysis slice (text) goes to the backend on phrase. That is a different SDK/posture review.

Do not add a second cloud STT path, and do not stream audio to Anthropic, Databricks, or a news vendor.

---

## What this is not

- Not a debug “record everything” mode. Raw audio still must not be dumped to disk, logs, or fixtures of real meetings.
- Not speaker identification.
- Not analysis of every utterance. Orchestration runs only on a certain trigger phrase.
- Not remote Start/Pause/Resume/Stop.

---

## Open items (do not guess in code)

- Exact trigger phrase wording.
- Whether Pause/Resume should play a short local cue (no network).
- Whether Stop should write a session with no questions (yes in this spec: finalize whatever transcript exists).
- Revisit STT placement if Legal will not accept Azure hearing unpaused session audio.

Confirm those before Sprint 2/3 implementation, not after.
