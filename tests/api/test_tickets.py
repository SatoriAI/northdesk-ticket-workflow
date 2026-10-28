from decimal import Decimal

import httpx

from northdesk.domain.models import Decision, Step, TicketState
from tests.conftest import FakeTemporalClient

T1 = {"ticket_id": "T-1", "customer_id": "C-200", "subject": "Can't log in", "body": "I forgot my password."}


async def test_create_ticket__new_returns_202_with_location(client: httpx.AsyncClient) -> None:
    response = await client.post("/tickets", json=T1)

    assert response.status_code == 202
    assert response.headers["location"] == "/tickets/T-1"
    assert response.json() == {"ticket_id": "T-1", "workflow_id": "ticket-T-1"}


async def test_create_ticket__running_duplicate_returns_200_same_workflow(client: httpx.AsyncClient) -> None:
    first = await client.post("/tickets", json=T1)
    second = await client.post("/tickets", json=T1)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["workflow_id"] == first.json()["workflow_id"]
    assert second.headers["location"] == "/tickets/T-1"


async def test_create_ticket__finished_duplicate_returns_200(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    temporal_client.workflows["ticket-T-1"] = "closed"

    response = await client.post("/tickets", json=T1)

    assert response.status_code == 200
    assert response.json()["workflow_id"] == "ticket-T-1"


async def test_create_ticket__unknown_field_is_validation_problem(client: httpx.AsyncClient) -> None:
    response = await client.post("/tickets", json={**T1, "priority": "urgent"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert all(set(error) == {"loc", "msg", "type"} for error in body["errors"])


async def test_get_ticket__unknown_is_problem_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/tickets/T-404")

    assert response.status_code == 404
    assert response.json()["code"] == "ticket_not_found"


async def test_get_ticket__shows_a_nan_proposal_without_failing(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    """A model's NaN amount is escalated by validation; the stored proposal must still be displayable."""
    proposal = Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=Decimal("NaN"))
    temporal_client.states["ticket-T-6"] = TicketState(step=Step.COMPLETED, decision=proposal)

    response = await client.get("/tickets/T-6")

    assert response.status_code == 200
    assert response.json()["decision"]["amount_eur"] == "NaN"
