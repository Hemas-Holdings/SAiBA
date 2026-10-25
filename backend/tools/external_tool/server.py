"""External-intelligence MCP tool for the orchestrator.

With a `WebResearcher` (Phase 3, opt-in), the tool answers from trusted
public sources through a separate Claude instance whose only tool is
restricted web search; see `tools.external_tool.agent`. Without one, it is
the Sprint 4 stub and returns a fixed response, so the orchestration path
can be tested end to end without web access. Either way it implements the
contract in `tools.contract`: it sees only the question, never the
transcript or internal (Genie) results, and logs neither question nor answer.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from tools.contract import QUESTION_SCHEMA, error_result, question_of, text_result, tool_id
from tools.external_tool.agent import ExternalUnavailable, WebResearcher

logger = logging.getLogger(__name__)

SERVER_NAME = "external"
TOOL_NAME = "search_external"
TOOL_ID = tool_id(SERVER_NAME, TOOL_NAME)
SOURCE = "external news (stub)"
WEB_SOURCE = "external web search"
STUB_ANSWER = "Analysts expect Sri Lankan consumer demand to grow about 4 percent next year."
_DESCRIPTION = (
    "Answer a question from external sources such as news, markets and "
    "public information about competitors and the economy."
)


@tool(TOOL_NAME, _DESCRIPTION, QUESTION_SCHEMA)
async def search_external(args: dict[str, Any]) -> dict[str, Any]:
    if question_of(args) is None:
        return error_result(SOURCE, "a question is required")
    return text_result(SOURCE, STUB_ANSWER)


def web_tool(researcher: WebResearcher) -> Any:
    """The same tool backed by restricted web search."""

    @tool(TOOL_NAME, _DESCRIPTION, QUESTION_SCHEMA)
    async def search_external_web(args: dict[str, Any]) -> dict[str, Any]:
        question = question_of(args)
        if question is None:
            return error_result(WEB_SOURCE, "a question is required")
        try:
            answer = await researcher.research(question)
        except ExternalUnavailable as error:
            logger.warning("external research unavailable reason=%s", error)
            return error_result(WEB_SOURCE, "no answer from public sources")
        except Exception as error:  # noqa: BLE001 - tools never raise; log the class only
            logger.warning("external research failed error=%s", type(error).__name__)
            return error_result(WEB_SOURCE, "search unavailable")
        return text_result(WEB_SOURCE, answer.render())

    return search_external_web


def build_server(researcher: WebResearcher | None = None) -> Any:
    """The in-process MCP server config for `ClaudeAgentOptions.mcp_servers`."""

    handler = web_tool(researcher) if researcher is not None else search_external
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.2.0", tools=[handler])
