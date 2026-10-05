# .cursorrules — Board Knowledge Assistant

## Project context

This is a board-meeting AI assistant with two components:
- `desktop-app/`: Python desktop app, local Start / Pause / Resume / Stop. While running, it captures meeting audio, streams it to the private backend for continuous transcription, and never writes audio to disk.
- `backend/`: Claude Agent SDK orchestration engine (Python). Continuous STT maintains a session transcript. On trigger phrase, the transcript through the question is analyzed. The assistant response is appended to that same transcript. Internal questions go to a Databricks Genie agent; external questions go to a separate intelligence agent. Durable copies go to Unity Catalog only.

Session behavior is defined in `docs/session-model.md`. Treat this as a security- and privacy-sensitive codebase. Board meeting content is the most sensitive data category in the company this system will touch. When in doubt, choose the more conservative, more auditable option, and flag the tradeoff rather than silently picking one.

## Non-negotiable security rules

- **Never persist raw audio to disk**, in `desktop-app/` or `backend/`. In-memory capture / stream buffers must stay that way. Do not add a "for debugging" audio dump, ever, even temporarily.
- **Pause is a capture boundary.** While paused, no audio is captured or streamed, STT does not run, and nothing is appended to the session transcript except an explicit gap marker. Resume continues the same session. Stop ends it and clears memory after the governed write.
- **Start / Pause / Resume / Stop are local actions only.** They must not be triggerable over the network.
- **Analysis runs only on a certain trigger phrase.** Continuous transcription is not continuous orchestration. If phrase detection or the transport layer is uncertain, do not invoke analysis. If streaming fails, stop sending audio (fail closed); never flush buffered meeting audio "just in case."
- **Never log full transcript content** at INFO level or below. Logs may record that a session started, paused, resumed, stopped, or that a trigger fired or a tool was called — not the words themselves outside the governed storage write path.
- **Never hardcode credentials.** All API keys (Azure Speech, Anthropic, Databricks) come from the secrets manager / environment configuration, never committed, never inline in code.
- **The Genie tool wrapper must be read-only at the code level, not just by convention.** Any code path that could construct a write/update/delete SQL statement against Unity Catalog through the Genie integration is a bug, flag it immediately if you see one being written.
- **Internal and external agent contexts must never share state.** Do not pass Genie query results into the external agent's context, or web search results into the Genie agent's context, or refactor them to share a common context object. They are separate MCP tools for a reason, keep the boundary in the code, not just in the architecture doc.
- **All backend network traffic uses TLS.** All desktop-app-to-backend traffic is authenticated (mutual TLS or scoped token), never an open or shared static-key endpoint.
- **No telemetry or analytics SDKs** that phone home usage data by default, if a library wants to send anonymous usage stats, disable it explicitly in configuration.

## Code style

- Python 3.11+, type hints on all function signatures, no exceptions for "quick scripts" in this repo.
- Async/await for all I/O-bound code (audio streaming, network calls, backend orchestration), don't mix in blocking calls without a documented reason.
- Prefer explicit, verbose code over clever one-liners in the capture and transport modules specifically, these are the modules most likely to be audited by someone outside the immediate dev team.
- Every module that touches audio, transcripts, or PII-adjacent data needs a module-level docstring stating what data it handles and where that data goes next.

## Testing requirements

- Every PR touching `desktop-app/capture/`, `desktop-app/wakeword/`, session pause/resume, or `backend/storage/` requires a test demonstrating the security property it's supposed to preserve (e.g., "paused audio is absent from the transcript," "write path enforces board-tier ACL"), not just functional correctness.
- Mock external services (Azure Speech, Databricks Genie) in unit tests, never call real APIs in CI.
- Integration tests against real services run only in a dedicated test environment with synthetic data, never against anything resembling real board content.

## Architecture boundaries, don't cross these without a documented decision

- `desktop-app/` and `backend/` communicate only through the defined transport contract in `backend/orchestration/`, no backend code should assume anything about how the desktop app captured or processed audio.
- The MCP tool interface (defined in Sprint 4 of the phased plan) is the contract Phase 2 and 3 tools implement against. Don't special-case the orchestration engine's logic around one specific tool's internals, if Genie needs something special, that goes in the Genie MCP wrapper, not in the orchestration engine itself.
- Don't add new external dependencies (new vendor APIs, new SDKs) without checking whether they change the security posture described in `docs/`. This project has already gone through several rounds of vendor-risk analysis, a new dependency is a new thing that analysis needs to cover.
- The analysis payload is the **transcript slice** (text through the question), not a raw-audio recording of the meeting. Do not add a parallel "send the whole meeting as audio" path.

## What NOT to do, even if asked

- Don't add a debug flag or verbose logging mode that captures raw audio or dumps full transcript content to logs or disk, even for local development. Use synthetic test fixtures instead. Continuous session STT is the product path, not a license to persist extra copies.
- Don't implement speaker identification, voice enrollment, or any biometric matching. This system is explicitly designed to never identify individual speakers. If a task seems to require it, stop and flag it rather than implementing it.
- Don't add write access to Unity Catalog from the Genie tool wrapper for "convenience" (e.g., logging query history back into the same schema it reads from). Use a separate, clearly-labeled write path if that's ever needed.
- Don't merge the internal and external agent into a single agent with combined tool access, "for simplicity." The separation is intentional.
- Don't skip Pause by "keeping the stream warm" or continuing STT while the UI shows paused.

## When unsure

Flag the ambiguity and ask, don't guess toward the more permissive option. This is the one project in the codebase where "add a TODO and move on" is worse than pausing to confirm.
