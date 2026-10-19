"""Backend session ingress and the orchestration engine.

`receive` is the TLS WebSocket contract for session control and mixed PCM.
`live` turns that audio into final utterances on the in-memory transcript
and, on a certain phrase, answers in the background. `question` pulls the
question out of a frozen slice. `orchestrator` answers it with the Claude
Agent SDK and the MCP tools in `tools`. Audio and transcript text are not
written to disk or logged here.
"""
