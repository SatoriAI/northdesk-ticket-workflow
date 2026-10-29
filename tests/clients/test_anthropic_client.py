"""The real LLM client against a mocked Anthropic API (the SDK's own HTTP transport), so it runs offline."""

import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

import anthropic
import httpx2
import pytest

from northdesk.agent.loop import run_agent
from northdesk.clients.llm.anthropic_client import AnthropicLlm
from northdesk.clients.llm.base import LlmRejectedError, LlmUnavailableError
from tests.support import REPO, toolbox_for

Handler = Callable[[httpx2.Request], httpx2.Response]
THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}


def message(*content: dict[str, Any], stop_reason: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": list(content),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def tool_use(name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": arguments}


class FakeApi:
    """Answers each request with the next scripted response and records what was sent."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int) -> dict[str, Any]:
        sent: dict[str, Any] = json.loads(self.requests[index].content)
        return sent


def llm_for(api: Handler) -> AnthropicLlm:
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(api))
    return AnthropicLlm(api_key="test-key", model="claude-opus-5-5", timeout_seconds=5, http_client=http)


async def complete(llm: AnthropicLlm) -> Any:
    return await llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])


async def test_complete__tool_calls_and_raw_content_for_the_next_turn() -> None:
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}}
    api = FakeApi(httpx2.Response(HTTPStatus.OK, json=message(fallback, THINKING, tool_use("get_customer"))))

    turn = await complete(llm_for(api))

    assert [(c.name, c.arguments) for c in turn.tool_calls] == [("get_customer", {})]
    assert [block.type for block in turn.raw_content] == ["thinking", "tool_use"]  # fallback marker left out


async def test_complete__request_shape() -> None:
    api = FakeApi(httpx2.Response(HTTPStatus.OK, json=message({"type": "text", "text": "ok"}, stop_reason="end_turn")))

    await complete(llm_for(api))

    body = api.body(0)
    assert (body["model"], body["fallbacks"], body["output_config"]) == (
        "claude-opus-5-5",
        "default",
        {"effort": "medium"},
    )
    assert "tool_choice" not in body  # forced tool choice is rejected by this model; the prompt steers instead
    assert "server-side-fallback-2026-07-01" in api.requests[0].headers["anthropic-beta"]


@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.CONFLICT,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.SERVICE_UNAVAILABLE,
        529,  # Anthropic's "overloaded"; not a standard status, so HTTPStatus has no member for it
    ],
)
async def test_complete__temporary_failure_is_unavailable_and_not_retried_by_the_sdk(status: int) -> None:
    api = FakeApi(httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "x"}}))

    with pytest.raises(LlmUnavailableError):
        await complete(llm_for(api))

    assert len(api.requests) == 1  # Temporal owns retries


@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ],
)
async def test_complete__permanent_failure_is_rejected(status: int) -> None:
    api = FakeApi(httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "x"}}))

    with pytest.raises(LlmRejectedError):
        await complete(llm_for(api))


@pytest.mark.parametrize("error", [httpx2.ConnectError("refused"), httpx2.ReadTimeout("slow")])
async def test_complete__transport_failure_is_unavailable(error: Exception) -> None:
    with pytest.raises(LlmUnavailableError):
        await complete(llm_for(FakeApi(error)))


async def test_complete__refusal_is_rejected() -> None:
    api = FakeApi(httpx2.Response(HTTPStatus.OK, json=message({"type": "text", "text": ""}, stop_reason="refusal")))

    with pytest.raises(LlmRejectedError, match="declined"):
        await complete(llm_for(api))


async def test_run_agent__real_client_loop_echoes_thinking_and_returns_the_decision() -> None:
    decision = {
        "category": "account",
        "priority": "normal",
        "action": "reply",
        "message": "Reset it.",
        "kb_article_ids": ["KB-1"],
    }
    api = FakeApi(
        httpx2.Response(HTTPStatus.OK, json=message(THINKING, tool_use("get_customer"))),
        httpx2.Response(HTTPStatus.OK, json=message(tool_use("submit_decision", **decision))),
    )
    ticket = REPO.tickets["T-1"]

    result = await run_agent(llm_for(api), ticket, toolbox_for(ticket))

    assert (result.action, result.kb_article_ids) == ("reply", ["KB-1"])
    assistant, tool_results = api.body(1)["messages"][1:3]
    assert assistant["content"][0] == THINKING  # passed back unchanged, as the API requires
    assert tool_results["content"][0]["tool_use_id"] == "toolu_get_customer"
    assert json.loads(tool_results["content"][0]["content"])["customer_id"] == "C-200"
