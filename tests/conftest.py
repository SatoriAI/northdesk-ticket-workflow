from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from starlette.types import ASGIApp

from northdesk.api.deps import get_temporal_client
from northdesk.main import create_app
from northdesk.settings import ApiSettings, TemporalSettings


class FakeServiceClient:
    def __init__(self) -> None:
        self.serving = True

    async def check_health(self, **_: Any) -> bool:
        return self.serving


class FakeTemporalClient:
    """Stands in for temporalio.client.Client at the API boundary; tests extend it as endpoints need."""

    def __init__(self) -> None:
        self.service_client = FakeServiceClient()


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
