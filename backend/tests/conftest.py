"""Shared backend fixtures. The fake Agent SDK never starts the Claude Code CLI."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage


def result_message(
    text: str | None,
    *,
    subtype: str = "success",
    is_error: bool = False,
    api_error_status: int | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="fake-session",
        result=text,
        api_error_status=api_error_status,
    )


class FakeQuery:
    """Stands in for `claude_agent_sdk.query` and records each call.

    `respond` builds the messages for one call from the prompt and options;
    it may await the real stub tool handlers to model tool use.
    """

    def __init__(
        self,
        respond: Callable[[str, Any], Awaitable[list[Any]]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._respond = respond
        self._error = error

    async def __call__(self, *, prompt: str, options: Any = None) -> AsyncIterator[Any]:
        self.calls.append((prompt, options))
        if self._error is not None:
            raise self._error
        messages = await self._respond(prompt, options) if self._respond else []
        for message in messages:
            yield message


@pytest.fixture
def fake_query() -> Callable[..., FakeQuery]:
    return FakeQuery
