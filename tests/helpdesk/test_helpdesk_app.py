from typing import Any

import httpx

from tests.helpdesk.conftest import FAIL_BEFORE, LOSE_RESPONSE, OK

REFUND: dict[str, Any] = {
    "ticket_id": "T-2",
    "action": "refund",
    "details": {"invoice_id": "INV-1002", "amount_eur": "49.00"},
}
KEY = {"Idempotency-Key": "ticket-T-2:refund"}


async def actions(helpdesk: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await helpdesk.get("/actions")
    result: list[dict[str, Any]] = response.json()
    return result


async def test_perform__repeated_key_replays_without_performing_again(
    helpdesk: httpx.AsyncClient, rolls: list[float]
) -> None:
    rolls.extend(OK + OK)

    first = await helpdesk.post("/actions", json=REFUND, headers=KEY)
    second = await helpdesk.post("/actions", json=REFUND, headers=KEY)

    assert (first.json()["replayed"], second.json()["replayed"]) == (False, True)
    assert second.json()["action_id"] == first.json()["action_id"]
    assert len(await actions(helpdesk)) == 1


async def test_perform__temporary_failure_performs_nothing(helpdesk: httpx.AsyncClient, rolls: list[float]) -> None:
    rolls.extend(FAIL_BEFORE)

    response = await helpdesk.post("/actions", json=REFUND, headers=KEY)

    assert response.status_code == 503
    assert await actions(helpdesk) == []


async def test_perform__lost_response_has_still_performed(helpdesk: httpx.AsyncClient, rolls: list[float]) -> None:
    rolls.extend(LOSE_RESPONSE)

    response = await helpdesk.post("/actions", json=REFUND, headers=KEY)

    assert response.status_code == 503
    assert [action["idempotency_key"] for action in await actions(helpdesk)] == ["ticket-T-2:refund"]


async def test_perform__missing_key_is_rejected(helpdesk: httpx.AsyncClient) -> None:
    response = await helpdesk.post("/actions", json=REFUND)

    assert response.status_code == 422
