"""The API against a real (time-skipping) Temporal server and worker, so the fake client cannot drift from what
Temporal actually returns: e.g. an Update to a finished workflow comes back as NOT_FOUND, like an unknown one."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import ASGIApp
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.api import tickets as tickets_api
from northdesk.api.deps import get_temporal_client
from northdesk.settings import ApiSettings
from northdesk.workflows.ticket_workflow import TicketWorkflow
from tests.workflows.test_ticket_workflow import Stubs

T2 = {"ticket_id": "T-2", "customer_id": "C-100", "subject": "Charged twice", "body": "Please refund the extra."}


@pytest.fixture
async def api(
    app: tuple[FastAPI, ASGIApp], env: WorkflowEnvironment, test_settings: ApiSettings
) -> AsyncIterator[httpx.AsyncClient]:
    fastapi_app, asgi_app = app
    fastapi_app.dependency_overrides[get_temporal_client] = lambda: env.client
    stubs = Stubs()
    try:
        async with (
            Worker(
                env.client,
                task_queue=test_settings.temporal.task_queue,  # the queue the API starts workflows on
                workflows=[TicketWorkflow],
                activities=[stubs.load_context, stubs.decide, stubs.act],
            ),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client,
        ):
            yield client
    finally:
        fastapi_app.dependency_overrides.clear()


async def wait_for(api: httpx.AsyncClient, ticket_id: str, step: str) -> dict[str, Any]:
    for _ in range(200):
        state: dict[str, Any] = (await api.get(f"/tickets/{ticket_id}")).json()
        if state.get("step") == step:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"{ticket_id} never reached {step}")


async def test_refund_flow__post_wait_approve_and_refuse_a_second_answer(api: httpx.AsyncClient) -> None:
    assert (await api.post("/tickets", json=T2)).status_code == 202
    await wait_for(api, "T-2", "awaiting_approval")

    approved = await api.post("/tickets/T-2/approval", json={"decision": "approve", "comment": "confirmed"})
    assert approved.status_code == 200
    done = await wait_for(api, "T-2", "completed")
    assert (done["result"]["action"], done["approval"]["comment"]) == ("refund", "confirmed")

    again = await api.post("/tickets/T-2/approval", json={"decision": "approve", "comment": "confirmed"})
    assert (again.status_code, again.json()["code"]) == (409, "ticket_not_awaiting_approval")
    assert (await api.post("/tickets", json=T2)).status_code == 200


async def test_approve__unknown_ticket_on_temporal_is_404(api: httpx.AsyncClient) -> None:
    response = await api.post("/tickets/T-404/approval", json={"decision": "approve"})

    assert (response.status_code, response.json()["code"]) == (404, "ticket_not_found")


@pytest.fixture
async def api_without_worker(
    app: tuple[FastAPI, ASGIApp], env: WorkflowEnvironment, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """A real Temporal server with no worker: workflows start, but nobody answers a query or an Update."""
    monkeypatch.setattr(tickets_api, "CALL_TIMEOUT", timedelta(seconds=1))
    fastapi_app, asgi_app = app
    fastapi_app.dependency_overrides[get_temporal_client] = lambda: env.client
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.clear()


async def test_no_worker__get_and_approval_are_problem_503(api_without_worker: httpx.AsyncClient) -> None:
    assert (await api_without_worker.post("/tickets", json=T2)).status_code == 202

    got = await api_without_worker.get("/tickets/T-2")
    approved = await api_without_worker.post("/tickets/T-2/approval", json={"decision": "approve"})

    assert (got.status_code, got.json()["code"]) == (503, "dependency_unavailable")
    assert (approved.status_code, approved.json()["code"]) == (503, "dependency_unavailable")
    assert "check GET before resending" in approved.json()["detail"]
