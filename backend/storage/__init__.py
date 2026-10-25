"""Governed storage for board sessions (Sprint 6).

`board` defines the write contract: what is stored for each answered
question and for each finished session. It is the running transcript
(meeting text, assistant turns, pause gap markers), the question, its
slice and answer, and timestamps. There is no audio field, and a payload
holding anything other than the contract's fields is refused.

`local` writes those records as files in a local folder, for development
and test only. Unity Catalog will implement the same contract for
production. See docs/session-model.md and docs/security-notes.md.
"""
