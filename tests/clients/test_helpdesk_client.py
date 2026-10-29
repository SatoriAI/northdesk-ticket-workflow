import json
from collections.abc import Callable
from http import HTTPStatus

import httpx
import pytest

from northdesk.clients.helpdesk.client import (
    HelpdeskClient,
    HelpdeskReceipt,
    HelpdeskRejectedError,
    HelpdeskUnavailableError,
)
from tests.support import REFUND


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> HelpdeskClient:
    return HelpdeskClient(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://helpdesk"))


async def perform(handler: Callable[[httpx.Request], httpx.Response]) -> HelpdeskReceipt:
    return await client_for(handler).perform(
        idempotency_key="ticket-T-2:refund", ticket_id="T-2", workflow_id="ticket-T-2", command=REFUND
    )


async def test_perform__sends_key_and_exact_amount() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(HTTPStatus.OK, json={"action_id": "A-1", "replayed": False})

    receipt = await perform(handler)

    assert receipt == HelpdeskReceipt(action_id="A-1", replayed=False)
    assert sent[0].headers["Idempotency-Key"] == "ticket-T-2:refund"
    assert json.loads(sent[0].content) == {
        "ticket_id": "T-2",
        "workflow_id": "ticket-T-2",
        "action": "refund",
        "details": {"category": "billing", "priority": "normal", "invoice_id": "INV-1002", "amount_eur": "49.00"},
    }


async def test_perform__replayed_key_is_reported() -> None:
    receipt = await perform(lambda _: httpx.Response(HTTPStatus.OK, json={"action_id": "A-1", "replayed": True}))

    assert receipt.replayed is True


@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.CONFLICT,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
    ],
)
async def test_perform__server_error_is_temporary(status: int) -> None:
    with pytest.raises(HelpdeskUnavailableError):
        await perform(lambda _: httpx.Response(status))


async def test_perform__connection_error_is_temporary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(HelpdeskUnavailableError):
        await perform(handler)


@pytest.mark.parametrize("status", [HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND, HTTPStatus.UNPROCESSABLE_ENTITY])
async def test_perform__client_error_is_rejected(status: int) -> None:
    with pytest.raises(HelpdeskRejectedError):
        await perform(lambda _: httpx.Response(status))
