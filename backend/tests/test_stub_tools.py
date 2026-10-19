"""Segments D5/D6: stub MCP tools return fixed responses under the shared contract."""

from __future__ import annotations

import pytest

from tools import contract
from tools.external_tool import server as external
from tools.genie_tool import server as internal

_TOOLS = [
    (internal, internal.query_internal_data),
    (external, external.search_external),
]


@pytest.mark.parametrize(("module", "stub"), _TOOLS)
async def test_fixed_response_is_labelled_with_its_source(module, stub) -> None:
    result = await stub.handler({"question": "what was Q3 revenue"})
    assert "is_error" not in result
    assert result["content"] == [
        {"type": "text", "text": f"[source: {module.SOURCE}] {module.STUB_ANSWER}"}
    ]


@pytest.mark.parametrize(("module", "stub"), _TOOLS)
@pytest.mark.parametrize("args", [{}, {"question": "  "}, {"question": 3}])
async def test_missing_question_is_an_error_result_not_an_exception(module, stub, args) -> None:
    result = await stub.handler(args)
    assert result["is_error"] is True
    assert module.SOURCE in result["content"][0]["text"]


@pytest.mark.parametrize(("module", "stub"), _TOOLS)
def test_tool_matches_the_contract(module, stub) -> None:
    assert stub.name == module.TOOL_NAME
    assert stub.input_schema == contract.QUESTION_SCHEMA
    assert module.TOOL_ID == f"mcp__{module.SERVER_NAME}__{module.TOOL_NAME}"
    server = module.build_server()
    assert server["type"] == "sdk"
    assert server["name"] == module.SERVER_NAME


def test_question_of_strips_and_validates() -> None:
    assert contract.question_of({"question": " hi "}) == "hi"
    assert contract.question_of("not a dict") is None  # type: ignore[arg-type]
