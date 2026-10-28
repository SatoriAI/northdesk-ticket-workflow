from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from northdesk.agent.loop import MAX_TURNS
from northdesk.clients.helpdesk.client import HelpdeskClient, HelpdeskUnavailableError
from northdesk.clients.llm.base import LlmClient, LlmTurn
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.domain.models import Action, ActionResult, Category, Command, Decision, Priority
from northdesk.workflows.activities import (
    HELPDESK_REJECTED,
    UNPARSEABLE_DECISION,
    ActInput,
    DecideInput,
    TicketActivities,
)
from tests.agent.conftest import ScriptedLlm

REFUND = Command(Action.REFUND, Category.BILLING, Priority.NORMAL, invoice_id="INV-1002", amount_eur=Decimal("49.00"))


async def act_with(handler: Callable[[httpx.Request], httpx.Response]) -> ActionResult:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://helpdesk")
    activities = TicketActivities(DataRepository.load(), FakeLlm(), HelpdeskClient(http))
    return await ActivityEnvironment().run(
        activities.act, ActInput(ticket_id="T-2", command=REFUND, idempotency_key="ticket-T-2:refund")
    )


async def test_act__rejected_request_is_not_retried() -> None:
    with pytest.raises(ApplicationError) as raised:
        await act_with(lambda _: httpx.Response(422))

    assert raised.value.non_retryable
    assert raised.value.type == HELPDESK_REJECTED


async def test_act__unavailable_helpdesk_is_retried() -> None:
    with pytest.raises(HelpdeskUnavailableError):
        await act_with(lambda _: httpx.Response(503))


async def test_act__sends_the_key_it_was_given() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"action_id": "A-1", "replayed": True})

    result = await act_with(handler)

    assert keys == ["ticket-T-2:refund"]
    assert result.replayed is True


async def decide_with(llm: LlmClient, ticket_id: str) -> Decision:
    repo = DataRepository.load()
    ticket = repo.tickets[ticket_id]
    activities = TicketActivities(repo, llm, HelpdeskClient(httpx.AsyncClient()))
    context = await ActivityEnvironment().run(activities.load_context, ticket)
    return await ActivityEnvironment().run(activities.decide, DecideInput(ticket=ticket, context=context))


async def test_decide__fake_llm_runs_the_agent_loop() -> None:
    decision = await decide_with(FakeLlm(), "T-2")

    assert (decision.action, decision.invoice_id) == ("refund", "INV-1002")


async def test_decide__no_usable_decision_is_not_retried() -> None:
    llm = ScriptedLlm(*[LlmTurn(text="I am not sure.")] * MAX_TURNS)

    with pytest.raises(ApplicationError) as raised:
        await decide_with(llm, "T-1")

    assert raised.value.non_retryable
    assert raised.value.type == UNPARSEABLE_DECISION
