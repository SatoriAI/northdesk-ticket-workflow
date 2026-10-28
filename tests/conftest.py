from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from starlette.types import ASGIApp
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from northdesk.api.deps import get_temporal_client
from northdesk.main import create_app
from northdesk.settings import ApiSettings, TemporalSettings
from northdesk.workflows.converter import DATA_CONVERTER


class FakeServiceClient:
    def __init__(self) -> None:
        self.serving = True

    async def check_health(self, **_: Any) -> bool:
        return self.serving


class FakeTemporalClient:
    """Stands in for temporalio.client.Client at the API boundary; tests extend it as endpoints need.

    start_workflow mimics the SDK behaviour that tests/workflows/test_starter.py pins against a real server.
    """

    def __init__(self) -> None:
        self.service_client = FakeServiceClient()
        self.workflows: dict[str, str] = {}  # workflow ID -> "running" | "closed"
        self.states: dict[str, Any] = {}  # workflow ID -> what its state query returns

    async def start_workflow(self, _workflow: Any, _arg: Any, *, id: str, **_: Any) -> SimpleNamespace:
        if self.workflows.get(id) == "closed":
            raise WorkflowAlreadyStartedError(id, "TicketWorkflow")
        started = id not in self.workflows
        self.workflows[id] = "running"
        return SimpleNamespace(_start_workflow_response=SimpleNamespace(started=started))

    def get_workflow_handle(self, workflow_id: str) -> SimpleNamespace:
        async def query(_query: Any) -> Any:
            if workflow_id not in self.states:
                raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
            return self.states[workflow_id]

        return SimpleNamespace(query=query)


@pytest.fixture(scope="session")
def test_settings() -> ApiSettings:
    # Nothing listens here: the lifespan's lazy client must never be used by the tests.
    return ApiSettings(enable_docs=False, temporal=TemporalSettings(address="127.0.0.1:1"))


@pytest.fixture(scope="session")
async def app(test_settings: ApiSettings) -> AsyncIterator[tuple[FastAPI, ASGIApp]]:
    """One app per test session, running its real lifespan."""
    fastapi_app = create_app(test_settings)
    async with LifespanManager(fastapi_app) as manager:
        yield fastapi_app, manager.app


@pytest.fixture
def temporal_client() -> FakeTemporalClient:
    return FakeTemporalClient()


@pytest.fixture
async def client(app: tuple[FastAPI, ASGIApp], temporal_client: FakeTemporalClient) -> AsyncIterator[httpx.AsyncClient]:
    fastapi_app, asgi_app = app
    fastapi_app.dependency_overrides[get_temporal_client] = lambda: temporal_client
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """Temporal's time-skipping test server, one per test (about 0.1 s each); the binary is downloaded once, then
    cached (see README for offline runs). Not shared: a test that ends with work still outstanding (an activity
    mid-run, a workflow no worker polls) stops a shared server from skipping time for every later test."""
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env
