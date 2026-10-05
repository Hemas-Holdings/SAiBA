"""Backend session ingress and, later, the orchestration engine.

`receive` is the TLS WebSocket contract for session control and mixed PCM.
`live` turns that audio into final utterances on the in-memory transcript.
The Claude Agent SDK is Sprint 4 and is not in this package yet. Audio and
transcript text are not written to disk or logged here.
"""
