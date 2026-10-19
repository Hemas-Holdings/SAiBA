"""Stub internal-data tool (Sprint 4). Replaced by Databricks Genie in Phase 2.

Returns a fixed response so the orchestration path can be tested end to end
without company data. Implements the contract in `tools.contract`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from tools.contract import QUESTION_SCHEMA, error_result, question_of, text_result, tool_id

SERVER_NAME = "internal"
TOOL_NAME = "query_internal_data"
TOOL_ID = tool_id(SERVER_NAME, TOOL_NAME)
SOURCE = "internal data (stub)"
STUB_ANSWER = "Q3 revenue was 12.4 billion rupees, up 8 percent on Q3 last year."


@tool(
    TOOL_NAME,
    "Answer a question from Hemas internal company data such as financials, "
    "operations and board papers.",
    QUESTION_SCHEMA,
)
async def query_internal_data(args: dict[str, Any]) -> dict[str, Any]:
    if question_of(args) is None:
        return error_result(SOURCE, "a question is required")
    return text_result(SOURCE, STUB_ANSWER)


def build_server() -> Any:
    """The in-process MCP server config for `ClaudeAgentOptions.mcp_servers`."""

    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=[query_internal_data])
