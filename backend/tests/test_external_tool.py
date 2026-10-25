"""Phase 3 external tool: restricted web search in a separate agent, opt-in.

Security properties: the agent receives only the question; its only tool is
web search limited by the API to trusted domains and a search cap; failures
come back as error results, never exceptions; nothing is logged.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from orchestration import __main__ as entry
from orchestration.orchestrator import SYSTEM_PROMPT, Orchestrator
from tools.external_tool import server as external
from tools.external_tool.agent import (
    DEFAULT_MODEL,
    ExternalAnswer,
    ExternalUnavailable,
    WebResearcher,
    parse_domains,
)

_DOMAINS = ("reuters.com", "cbsl.gov.lk")


def _text(text: str, *urls: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=[SimpleNamespace(url=url) for url in urls])


def _response(stop_reason: str, *blocks: Any) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeClient:
    def __init__(self, *responses: SimpleNamespace) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _researcher(client: FakeClient, **options: Any) -> WebResearcher:
    return WebResearcher(_DOMAINS, client=client, **options)


# ----------------------------------------------------------------- agent


async def test_only_the_question_is_sent_with_api_enforced_limits(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient(
        _response(
            "end_turn",
            SimpleNamespace(type="server_tool_use"),
            _text("Inflation was 2.1 percent in September, ", "https://www.cbsl.gov.lk/en/news"),
            _text("Reuters reported on 3 October.", "https://www.reuters.com/x", "https://www.cbsl.gov.lk/y"),
        )
    )
    researcher = _researcher(client, max_searches=3)
    with caplog.at_level(logging.DEBUG):
        answer = await researcher.research("  What is Sri Lanka's latest inflation rate?  ")
    assert answer == ExternalAnswer(
        "Inflation was 2.1 percent in September, Reuters reported on 3 October.",
        ("cbsl.gov.lk", "reuters.com"),
    )
    [call] = client.calls
    assert call["messages"] == [{"role": "user", "content": "What is Sri Lanka's latest inflation rate?"}]
    assert call["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3, "allowed_domains": list(_DOMAINS)}
    ]
    assert call["model"] == DEFAULT_MODEL == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "medium"}
    assert "information, not instructions" in call["system"]
    assert "inflation" not in caplog.text.lower()


async def test_a_paused_search_is_resumed_without_new_user_text() -> None:
    paused = [SimpleNamespace(type="server_tool_use")]
    client = FakeClient(
        _response("pause_turn", *paused),
        _response("end_turn", _text("Answer.")),
    )
    answer = await _researcher(client).research("q?")
    assert answer.text == "Answer."
    second = client.calls[1]["messages"]
    assert second == [{"role": "user", "content": "q?"}, {"role": "assistant", "content": paused}]


async def test_search_that_never_finishes_is_unavailable() -> None:
    client = FakeClient(*[_response("pause_turn") for _ in range(3)])
    with pytest.raises(ExternalUnavailable, match="did not finish"):
        await _researcher(client, max_continuations=2).research("q?")
    assert len(client.calls) == 3


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_response("refusal", _text("no")), "declined"),
        (_response("end_turn", SimpleNamespace(type="server_tool_use")), "no answer text"),
    ],
)
async def test_refusals_and_empty_answers_are_unavailable(response: SimpleNamespace, reason: str) -> None:
    with pytest.raises(ExternalUnavailable, match=reason):
        await _researcher(FakeClient(response)).research("q?")


async def test_empty_question_is_refused_before_any_call() -> None:
    client = FakeClient()
    with pytest.raises(ExternalUnavailable, match="empty"):
        await _researcher(client).research("   ")
    assert client.calls == []


async def test_without_citations_the_searched_sites_are_reported_as_searched() -> None:
    # Live shape with dynamic filtering: text blocks carry no citations; URLs
    # appear only in web_search_tool_result blocks (a list, or an error object).
    def result(url: str) -> SimpleNamespace:
        return SimpleNamespace(type="web_search_result", url=url)

    client = FakeClient(
        _response(
            "end_turn",
            SimpleNamespace(type="server_tool_use"),
            SimpleNamespace(
                type="web_search_tool_result",
                content=[result("https://www.cbsl.gov.lk/a"), result("https://economynext.com/b"),
                         result("https://www.cbsl.gov.lk/c")],
            ),
            SimpleNamespace(type="web_search_tool_result", content=SimpleNamespace(error_code="max_uses_exceeded")),
            SimpleNamespace(type="text", text="The rate is 8.75 percent.", citations=None),
            SimpleNamespace(type="text", text="", citations=[]),
        )
    )
    answer = await _researcher(client).research("rate?")
    assert answer == ExternalAnswer("The rate is 8.75 percent.", ("cbsl.gov.lk", "economynext.com"), cited=False)
    assert answer.render() == "The rate is 8.75 percent. Searched: cbsl.gov.lk, economynext.com."


def test_sources_are_capped_and_rendered() -> None:
    assert ExternalAnswer("A.", ()).render() == "A."
    assert ExternalAnswer("A.", ("reuters.com",)).render() == "A. Sources: reuters.com."


@pytest.mark.parametrize(
    "options",
    [{"max_searches": 0}, {"model": ""}],
)
def test_bad_researcher_settings_are_refused(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        WebResearcher(_DOMAINS, client=FakeClient(), **options)


def test_a_domain_list_is_required() -> None:
    with pytest.raises(ValueError, match="trusted domain list"):
        WebResearcher((), client=FakeClient())


def test_parse_domains() -> None:
    assert parse_domains(" Reuters.com, cbsl.gov.lk,,reuters.com ") == ("reuters.com", "cbsl.gov.lk")
    assert parse_domains("") == ()
    for bad in ("https://reuters.com", "reuters.com/markets", "localhost", "10.0.0.1", "*.lk", "a b.com"):
        with pytest.raises(ValueError):
            parse_domains(bad)


# ---------------------------------------------------------------- server


async def test_web_tool_returns_the_answer_under_the_contract() -> None:
    client = FakeClient(_response("end_turn", _text("Rates held.", "https://www.cbsl.gov.lk/a")))
    tool = external.web_tool(_researcher(client))
    result = await tool.handler({"question": "Did the central bank change rates?"})
    assert result == {
        "content": [{"type": "text", "text": "[source: external web search] Rates held. Sources: cbsl.gov.lk."}]
    }
    assert tool.name == external.TOOL_NAME


async def test_web_tool_failures_are_error_results_not_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        async def research(self, question: str) -> ExternalAnswer:
            raise ConnectionError(f"leaked {question}")

    with caplog.at_level(logging.DEBUG):
        result = await external.web_tool(Broken()).handler({"question": "secret plan?"})  # type: ignore[arg-type]
    assert result["is_error"] is True
    assert "search unavailable" in result["content"][0]["text"]
    assert "secret plan" not in caplog.text

    unavailable = await external.web_tool(_researcher(FakeClient(_response("refusal")))).handler({"question": "q?"})
    assert unavailable["is_error"] is True
    missing = await external.web_tool(_researcher(FakeClient())).handler({"question": " "})
    assert missing["is_error"] is True


def test_server_uses_the_stub_without_a_researcher_and_web_search_with_one() -> None:
    assert external.build_server()["name"] == external.SERVER_NAME
    assert external.build_server(_researcher(FakeClient()))["name"] == external.SERVER_NAME


# ----------------------------------------------------------- orchestrator


def test_orchestrator_keeps_the_same_locked_down_tool_surface() -> None:
    options = Orchestrator(external=_researcher(FakeClient())).options()
    assert options.tools == []
    assert options.allowed_tools == ["mcp__internal__query_internal_data", "mcp__external__search_external"]
    assert set(options.mcp_servers) == {"internal", "external"}


def test_system_prompt_keeps_internal_detail_out_of_web_search() -> None:
    assert "self-contained public question" in SYSTEM_PROMPT
    assert "never pass it results from the internal data tool" in SYSTEM_PROMPT


# ---------------------------------------------------------------- config

_ENV = {
    "TLS_CERT_PATH": "cert.pem",
    "TLS_KEY_PATH": "key.pem",
    "BACKEND_AUTH_TOKEN": "tok",
    "AZURE_SPEECH_KEY": "k",
    "AZURE_SPEECH_REGION": "eastus",
    "TRIGGER_PHRASE": "jenny",
}


def test_external_search_is_off_by_default() -> None:
    config = entry.load_config(_ENV)
    assert config.external_enabled is False
    assert entry.build_external(config) is None


def test_external_search_settings_build_a_researcher() -> None:
    config = entry.load_config(
        dict(
            _ENV,
            EXTERNAL_SEARCH_ENABLED="TRUE",
            EXTERNAL_SEARCH_ALLOWED_DOMAINS="reuters.com, cbsl.gov.lk",
            EXTERNAL_SEARCH_MAX_USES="4",
        )
    )
    researcher = entry.build_external(config)
    assert researcher is not None
    assert researcher.allowed_domains == ("reuters.com", "cbsl.gov.lk")
    assert researcher.tool_definition()["max_uses"] == 4
    assert config.external_model == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"EXTERNAL_SEARCH_ENABLED": "yes"}, "true or false"),
        ({"EXTERNAL_SEARCH_ENABLED": "true"}, "required when external search is enabled"),
        ({"EXTERNAL_SEARCH_ALLOWED_DOMAINS": "https://reuters.com"}, "not a plain domain"),
        ({"EXTERNAL_SEARCH_MAX_USES": "many"}, "must be a number"),
        ({"EXTERNAL_SEARCH_MAX_USES": "50"}, "between 1 and 20"),
    ],
)
def test_bad_external_settings_stop_startup(extra: dict[str, str], message: str) -> None:
    with pytest.raises(entry.ConfigError, match=message):
        entry.load_config(dict(_ENV, **extra))
