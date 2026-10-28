"""The ticket endpoints against a real (time-skipping) Temporal server, with the real workflow on stub activities."""

import asyncio
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest

from northdesk.api.tickets import _state_out
from northdesk.domain.models import Decision, Step, TicketState
from northdesk.log import ContextLogFilter

T1 = {"ticket_id": "T-1", "customer_id": "C-100", "subject": "Can't log in", "body": "I forgot my password."}
T2 = {"ticket_id": "T-2", "customer_id": "C-100", "subject": "Charged twice", "body": "Please refund the extra."}
SLOW = {"ticket_id": "T-slow", "customer_id": "C-100", "subject": "s", "body": "b"}  # the stub decide never returns


async def wait_for(api: httpx.AsyncClient, ticket_id: str, step: str) -> dict[str, Any]:
    for _ in range(200):
        state: dict[str, Any] = (await api.get(f"/tickets/{ticket_id}")).json()
        if state.get("step") == step:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"{ticket_id} never reached {step}")


# --- POST /tickets ---------------------------------------------------------------------------------------------


async def test_create_ticket__new_returns_202_with_location(api: httpx.AsyncClient) -> None:
    response = await api.post("/tickets", json=T1)

    assert response.status_code == 202
    assert response.headers["location"] == "/tickets/T-1"
    assert response.json() == {"ticket_id": "T-1", "workflow_id": "ticket-T-1"}


async def test_create_ticket__running_duplicate_returns_200_same_workflow(
    api_without_worker: httpx.AsyncClient,
) -> None:
    first = await api_without_worker.post("/tickets", json=T1)  # no worker: it stays running
    second = await api_without_worker.post("/tickets", json=T1)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["workflow_id"] == first.json()["workflow_id"]
    assert second.headers["location"] == "/tickets/T-1"


async def test_create_ticket__finished_duplicate_returns_200(api: httpx.AsyncClient) -> None:
    await api.post("/tickets", json=T1)
    await wait_for(api, "T-1", "completed")

    response = await api.post("/tickets", json=T1)

    assert (response.status_code, response.json()["workflow_id"]) == (200, "ticket-T-1")


async def test_create_ticket__unknown_field_is_validation_problem(api: httpx.AsyncClient) -> None:
    response = await api.post("/tickets", json={**T1, "priority": "urgent"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert all(set(error) == {"loc", "msg", "type"} for error in body["errors"])  # input is never echoed


# --- GET /tickets/{id} -----------------------------------------------------------------------------------------


async def test_get_ticket__shows_steps_decision_and_result(api: httpx.AsyncClient) -> None:
    await api.post("/tickets", json=T1)

    state = await wait_for(api, "T-1", "completed")

    assert (state["decision"]["action"], state["command"]["action"], state["result"]["action"]) == ("reply",) * 3
    assert [entry["step"] for entry in state["history"]][-1] == "completed"


async def test_get_ticket__unknown_is_problem_404(api: httpx.AsyncClient) -> None:
    response = await api.get("/tickets/T-404")

    assert (response.status_code, response.json()["code"]) == (404, "ticket_not_found")


def test_state_out__shows_a_nan_proposal_without_failing() -> None:
    """A model's NaN amount is escalated by validation; the stored proposal must still be displayable."""
    proposal = Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=Decimal("NaN"))

    shown = _state_out("T-6", "ticket-T-6", TicketState(step=Step.COMPLETED, decision=proposal))

    assert shown.model_dump(mode="json")["decision"]["amount_eur"] == "NaN"


# --- POST /tickets/{id}/approval -------------------------------------------------------------------------------


async def test_approve__refund_is_carried_out_and_a_second_answer_refused(api: httpx.AsyncClient) -> None:
    assert (await api.post("/tickets", json=T2)).status_code == 202
    await wait_for(api, "T-2", "awaiting_approval")

    approved = await api.post("/tickets/T-2/approval", json={"decision": "approve", "comment": "confirmed"})

    assert (approved.status_code, approved.json()["approval"]) == (200, {"decision": "approve", "comment": "confirmed"})
    done = await wait_for(api, "T-2", "completed")
    assert done["result"]["action"] == "refund"
    again = await api.post("/tickets/T-2/approval", json={"decision": "approve", "comment": "confirmed"})
    assert (again.status_code, again.json()["code"]) == (409, "ticket_not_awaiting_approval")
    assert again.json()["detail"] == "ticket has already finished"


async def test_approve__reject_tells_the_customer(api: httpx.AsyncClient) -> None:
    await api.post("/tickets", json=T2)
    await wait_for(api, "T-2", "awaiting_approval")

    await api.post("/tickets/T-2/approval", json={"decision": "reject"})

    done = await wait_for(api, "T-2", "completed")
    assert (done["command"]["action"], done["result"]["action"]) == ("reply", "reply")


async def test_approve__wrong_step_is_problem_409(api: httpx.AsyncClient) -> None:
    await api.post("/tickets", json=SLOW)
    await wait_for(api, "T-slow", "deciding")

    response = await api.post("/tickets/T-slow/approval", json={"decision": "approve"})

    assert (response.status_code, response.json()["code"]) == (409, "ticket_not_awaiting_approval")
    assert response.json()["detail"] == "ticket is not awaiting approval (step: deciding)"


async def test_approve__unknown_ticket_is_problem_404(api: httpx.AsyncClient) -> None:
    response = await api.post("/tickets/T-404/approval", json={"decision": "approve"})

    assert (response.status_code, response.json()["code"]) == (404, "ticket_not_found")


@pytest.mark.parametrize(
    "body", [{"decision": "maybe"}, {"decision": "approve", "comment": "x" * 501}, {"decision": "approve", "by": "me"}]
)
async def test_approve__malformed_answer_is_problem_422(api: httpx.AsyncClient, body: dict[str, object]) -> None:
    response = await api.post("/tickets/T-2/approval", json=body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")


# --- Temporal or the worker not answering ----------------------------------------------------------------------


async def test_get_and_approve__no_worker_is_problem_503(api_without_worker: httpx.AsyncClient) -> None:
    assert (await api_without_worker.post("/tickets", json=T2)).status_code == 202

    got = await api_without_worker.get("/tickets/T-2")
    approved = await api_without_worker.post("/tickets/T-2/approval", json={"decision": "approve"})

    assert (got.status_code, got.json()["code"]) == (503, "dependency_unavailable")
    assert (approved.status_code, approved.json()["code"]) == (503, "dependency_unavailable")
    assert "check GET before resending" in approved.json()["detail"]
    assert "approval" not in got.json()["detail"]  # the resend advice is only for the approval route


@pytest.mark.parametrize(("method", "path"), [("POST", "/tickets"), ("GET", "/tickets/T-1")])
async def test_tickets__unreachable_temporal_is_problem_503(
    unreachable_api: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await unreachable_api.request(method, path, json=T1 if method == "POST" else None)

    assert (response.status_code, response.json()["code"]) == (503, "dependency_unavailable")


# --- logs ------------------------------------------------------------------------------------------------------


async def test_logs__lines_about_a_ticket_carry_its_ids(
    api: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.handler.addFilter(ContextLogFilter())  # the filter logging.json installs on the real handler
    caplog.set_level(logging.INFO)

    response = await api.post("/tickets/T-404/approval", json={"decision": "approve"})

    # uvicorn's access line also runs in the request's context, so it gets the same fields; there is no uvicorn
    # here, so the error handler's line stands in for it.
    lines = [r for r in caplog.records if r.name == "northdesk.api.handlers"]
    assert lines
    for line in lines:
        assert (line.ticket_id, line.workflow_id) == ("T-404", "ticket-T-404")  # type: ignore[attr-defined]
        assert line.request_id == response.headers["x-request-id"]  # type: ignore[attr-defined]
