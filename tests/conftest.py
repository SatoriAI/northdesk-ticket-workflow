import os
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from starlette.types import ASGIApp
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.api import tickets as tickets_api
from northdesk.api.deps import get_temporal_client
from northdesk.main import create_app
from northdesk.settings import ApiSettings, TemporalSettings
from northdesk.workflows.client import logging_runtime
from northdesk.workflows.converter import DATA_CONVERTER
from northdesk.workflows.ticket_workflow import TicketWorkflow
from tests.support import Stubs


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """Temporal's time-skipping test server, one per test (about 0.1 s each). Not shared: a test that ends with
    work still outstanding (an activity mid-run, a workflow no worker polls) stops a shared server from skipping
    time for every later test."""
    # TEMPORAL_TEST_SERVER_DIR points at a pre-downloaded binary (the Docker test image sets it), so the tests do not
    # depend on what happens to be cached in the system temp directory. The runtime keeps core warnings quiet.
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=DATA_CONVERTER,
        download_dest_dir=os.environ.get("TEMPORAL_TEST_SERVER_DIR"),
        runtime=logging_runtime(),
    ) as env:
        yield env


@pytest.fixture(scope="session")
def api_settings() -> ApiSettings:
    # The app's own (lazy) client points at a closed port: tests swap in the test server's client, except the ones
    # that check what happens when Temporal cannot be reached.
    return ApiSettings(enable_docs=False, temporal=TemporalSettings(address="127.0.0.1:1"))


@pytest.fixture(scope="session")
async def app(api_settings: ApiSettings) -> AsyncIterator[tuple[FastAPI, ASGIApp]]:
    """One app per test session, running its real lifespan."""
    fastapi_app = create_app(api_settings)
    async with LifespanManager(fastapi_app) as manager:
        yield fastapi_app, manager.app


@pytest.fixture
async def unreachable_api(app: tuple[FastAPI, ASGIApp]) -> AsyncIterator[httpx.AsyncClient]:
    """The API with its own client, which cannot reach Temporal."""
    _, asgi_app = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def api_without_worker(
    app: tuple[FastAPI, ASGIApp], env: WorkflowEnvironment, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """The API on a real Temporal server with no worker: workflows start and stay put; queries and Updates go
    unanswered (and give up after 1 s instead of 10)."""
    monkeypatch.setattr(tickets_api, "CALL_TIMEOUT", timedelta(seconds=1))
    fastapi_app, asgi_app = app
    fastapi_app.dependency_overrides[get_temporal_client] = lambda: env.client
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
async def api(
    api_without_worker: httpx.AsyncClient, env: WorkflowEnvironment, api_settings: ApiSettings
) -> AsyncIterator[httpx.AsyncClient]:
    """The API on a real Temporal server, with a worker running the real workflow on stub activities."""
    stubs = Stubs()
    async with Worker(
        env.client,
        task_queue=api_settings.temporal.task_queue,  # the queue the API starts workflows on
        workflows=[TicketWorkflow],
        activities=[stubs.load_context, stubs.decide, stubs.act],
    ):
        yield api_without_worker
