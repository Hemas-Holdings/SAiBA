"""The MCP tool contract every internal and external tool implements.

Sprint 4 stubs and the Phase 2 (Genie) and Phase 3 (external agent) tools
share this shape so the orchestrator does not change when a stub is replaced:

- one in-process MCP server per tool family, under a fixed server name
- one tool per server, taking `{"question": str}`
- a result of MCP content with one text block, prefixed with the source label
- a failure is `{"content": [...], "is_error": True}`, never a raised exception

Tool handlers do not log question or result text.
"""

from __future__ import annotations

from typing import Any

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}


def text_result(source: str, text: str) -> dict[str, Any]:
    """A successful tool result labelled with its source."""

    return {"content": [{"type": "text", "text": f"[source: {source}] {text}"}]}


def error_result(source: str, reason: str) -> dict[str, Any]:
    """A failed tool result. `reason` must not contain meeting text."""

    return {
        "content": [{"type": "text", "text": f"[source: {source}] unavailable: {reason}"}],
        "is_error": True,
    }


def question_of(args: dict[str, Any]) -> str | None:
    """The validated question argument, or None."""

    value = args.get("question") if isinstance(args, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def tool_id(server: str, tool: str) -> str:
    """The name the Agent SDK uses to allow an MCP tool."""

    return f"mcp__{server}__{tool}"
