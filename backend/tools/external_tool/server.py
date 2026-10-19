"""Stub external-intelligence tool (Sprint 4). Replaced in Phase 3.

Returns a fixed response so the orchestration path can be tested end to end
without web access. Implements the contract in `tools.contract`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from tools.contract import QUESTION_SCHEMA, error_result, question_of, text_result, tool_id

SERVER_NAME = "external"
TOOL_NAME = "search_external"
TOOL_ID = tool_id(SERVER_NAME, TOOL_NAME)
SOURCE = "external news (stub)"
STUB_ANSWER = "Analysts expect Sri Lankan consumer demand to grow about 4 percent next year."


@tool(
    TOOL_NAME,
    "Answer a question from external sources such as news, markets and "
    "public information about competitors and the economy.",
    QUESTION_SCHEMA,
)
async def search_external(args: dict[str, Any]) -> dict[str, Any]:
    if question_of(args) is None:
        return error_result(SOURCE, "a question is required")
    return text_result(SOURCE, STUB_ANSWER)


def build_server() -> Any:
    """The in-process MCP server config for `ClaudeAgentOptions.mcp_servers`."""

    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=[search_external])
