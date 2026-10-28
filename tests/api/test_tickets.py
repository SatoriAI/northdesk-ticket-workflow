from dataclasses import replace
from decimal import Decimal

import httpx
import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from northdesk.domain.models import ApprovalRequest, Decision, Step, TicketState
from northdesk.workflows.ticket_workflow import NOT_AWAITING_APPROVAL
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


# --- approval --------------------------------------------------------------------------------------------------

WAITING = TicketState(step=Step.AWAITING_APPROVAL)


async def test_approve__forwards_the_answer_and_returns_the_state(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    temporal_client.states["ticket-T-2"] = replace(WAITING, approval=ApprovalRequest("approve", "ok"))

    response = await client.post("/tickets/T-2/approval", json={"decision": "approve", "comment": "ok"})

    assert response.status_code == 200
    assert temporal_client.updates == [("ticket-T-2", ApprovalRequest("approve", "ok"))]
    assert response.json()["approval"] == {"decision": "approve", "comment": "ok"}


async def test_approve__wrong_step_is_problem_409(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    refusal = ApplicationError("ticket is not awaiting approval (step: deciding)", type=NOT_AWAITING_APPROVAL)
    temporal_client.call_errors["ticket-T-1"] = WorkflowUpdateFailedError(refusal)

    response = await client.post("/tickets/T-1/approval", json={"decision": "approve"})

    assert response.status_code == 409
    assert response.json()["code"] == "ticket_not_awaiting_approval"
    assert response.json()["detail"] == "ticket is not awaiting approval (step: deciding)"


async def test_approve__finished_ticket_is_problem_409(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    temporal_client.workflows["ticket-T-1"] = "closed"

    response = await client.post("/tickets/T-1/approval", json={"decision": "reject"})

    assert (response.status_code, response.json()["code"]) == (409, "ticket_not_awaiting_approval")


async def test_approve__unknown_ticket_is_problem_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/tickets/T-404/approval", json={"decision": "approve"})

    assert (response.status_code, response.json()["code"]) == (404, "ticket_not_found")


@pytest.mark.parametrize(
    "body", [{"decision": "maybe"}, {"decision": "approve", "comment": "x" * 501}, {"decision": "approve", "by": "me"}]
)
async def test_approve__malformed_answer_is_problem_422(client: httpx.AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/tickets/T-2/approval", json=body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")


# --- no worker -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), [("GET", "/tickets/T-2"), ("POST", "/tickets/T-2/approval")])
async def test_call__no_worker_answering_is_problem_503(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient, method: str, path: str
) -> None:
    temporal_client.call_errors["ticket-T-2"] = RPCError("Timeout expired", RPCStatusCode.CANCELLED, b"")

    response = await client.request(method, path, json={"decision": "approve"} if method == "POST" else None)

    assert (response.status_code, response.json()["code"]) == (503, "dependency_unavailable")
