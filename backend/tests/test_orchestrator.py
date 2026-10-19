"""Segment D4: the orchestrator's SDK options, prompt, result handling, and privacy."""

from __future__ import annotations

import logging

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeSDKError, TextBlock

from orchestration.orchestrator import DEFAULT_MODEL, SYSTEM_PROMPT, Orchestrator
from tests.conftest import result_message
from tools.external_tool import server as external
from tools.genie_tool import server as internal

_SLICE = "the quarter was steady\nboard assistant what was Q3 revenue"
_QUESTION = "what was Q3 revenue"


async def test_answer_is_the_result_text_and_options_are_locked_down(fake_query) -> None:
    async def respond(prompt, options):
        return [
            AssistantMessage(content=[TextBlock(text="thinking aloud")], model=DEFAULT_MODEL),
            result_message("  Revenue was 12.4 billion rupees.  "),
        ]

    query = fake_query(respond)
    answer = await Orchestrator(query=query).answer(_SLICE, _QUESTION)
    assert answer == "Revenue was 12.4 billion rupees."

    prompt, options = query.calls[0]
    assert f"<question>{_QUESTION}</question>" in prompt
    assert "<transcript>\nthe quarter was steady" in prompt
    assert options.model == "claude-opus-5-5"
    assert options.system_prompt == SYSTEM_PROMPT
    assert options.tools == []
    assert options.allowed_tools == [internal.TOOL_ID, external.TOOL_ID]
    assert set(options.mcp_servers) == {internal.SERVER_NAME, external.SERVER_NAME}
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.extra_args == {"no-session-persistence": None}


@pytest.mark.parametrize(
    "result",
    [
        result_message(None, subtype="error_max_turns", is_error=True),
        result_message("partial", is_error=True, api_error_status=529),
        result_message(None),
    ],
)
async def test_failed_or_empty_result_gives_nothing_to_append(fake_query, result) -> None:
    async def respond(prompt, options):
        return [result]

    assert await Orchestrator(query=fake_query(respond)).answer(_SLICE, _QUESTION) == ""


async def test_sdk_error_fails_closed_and_logs_only_the_class(fake_query, caplog) -> None:
    query = fake_query(error=ClaudeSDKError("leaked what was Q3 revenue"))
    with caplog.at_level(logging.DEBUG):
        assert await Orchestrator(query=query).answer(_SLICE, _QUESTION) == ""
    assert "ClaudeSDKError" in caplog.text
    assert "Q3 revenue" not in caplog.text


async def test_blank_question_does_not_call_the_sdk(fake_query) -> None:
    query = fake_query()
    assert await Orchestrator(query=query).answer(_SLICE, "   ") == ""
    assert query.calls == []


async def test_answer_text_is_not_logged(fake_query, caplog) -> None:
    async def respond(prompt, options):
        return [result_message("Revenue was 12.4 billion rupees.")]

    with caplog.at_level(logging.DEBUG):
        await Orchestrator(query=fake_query(respond)).answer(_SLICE, _QUESTION)
    assert "12.4" not in caplog.text
    assert "Q3 revenue" not in caplog.text


def test_model_is_required() -> None:
    with pytest.raises(ValueError):
        Orchestrator(model="")


def test_default_query_is_the_sdk_query() -> None:
    import claude_agent_sdk

    from orchestration.orchestrator import _default_query

    assert _default_query() is claude_agent_sdk.query
