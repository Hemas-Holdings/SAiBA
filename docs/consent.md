# Consent and retention

Placeholder for Legal. This track starts in Sprint 1 and must not wait for the technical build to finish.

The product now transcribes the **unpaused session**, not only a triggered question clip. See [session-model.md](session-model.md). Do not sign off against the old clip-only description.

## Still to finalize with Legal

- In-room notice that capture and transcription are running (manual Start / Pause / Resume / Stop). Pause must be described as the way to keep a discussion out of the transcript.
- That Azure Speech (working default) hears unpaused session audio for continuous STT, not only the question.
- What is stored after a session / triggered question: the running transcript (meeting text + assistant answers + pause gap markers), the question slice, the answer — never raw audio.
- That assistant replies are part of the stored transcript and will be used as context for later questions in the same session.
- Retention period and deletion process for Unity Catalog board-tier records.
- Who may access those records.

Do not ship a pilot (Sprint 8) without Legal sign-off on this document.
