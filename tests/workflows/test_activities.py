from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from northdesk.clients.helpdesk.client import HelpdeskClient, HelpdeskUnavailableError
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.domain.models import Action, ActionResult, Category, Command, Priority
from northdesk.workflows.activities import HELPDESK_REJECTED, ActInput, TicketActivities

REFUND = Command(Action.REFUND, Category.BILLING, Priority.NORMAL, invoice_id="INV-1002", amount_eur=Decimal("49.00"))


async def act_with(handler: Callable[[httpx.Request], httpx.Response]) -> ActionResult:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://helpdesk")
    activities = TicketActivities(DataRepository.load(), FakeLlm(), HelpdeskClient(http))
    return await ActivityEnvironment().run(activities.act, ActInput(ticket_id="T-2", command=REFUND))


async def test_act__rejected_request_is_not_retried() -> None:
    with pytest.raises(ApplicationError) as raised:
        await act_with(lambda _: httpx.Response(422))

    assert raised.value.non_retryable
    assert raised.value.type == HELPDESK_REJECTED


async def test_act__unavailable_helpdesk_is_retried() -> None:
    with pytest.raises(HelpdeskUnavailableError):
        await act_with(lambda _: httpx.Response(503))


async def test_act__key_is_workflow_id_and_action() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"action_id": "A-1", "replayed": True})

    result = await act_with(handler)

    # ActivityEnvironment's default workflow ID is "test"
    assert keys == ["test:refund"]
    assert result.replayed is True
