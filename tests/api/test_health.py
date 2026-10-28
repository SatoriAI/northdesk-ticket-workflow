import httpx
from fastapi import FastAPI
from starlette.types import ASGIApp

from tests.conftest import FakeTemporalClient


async def test_live__ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready__ok_when_temporal_serves(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200


async def test_ready__problem_503_when_temporal_not_serving(
    client: httpx.AsyncClient, temporal_client: FakeTemporalClient
) -> None:
    temporal_client.service_client.serving = False

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "dependency_unavailable"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_unknown_route__problem_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope", headers={"X-Request-ID": "abc-123"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert response.headers["x-request-id"] == "abc-123"


async def test_tickets__unreachable_temporal_is_problem_503(app: tuple[FastAPI, ASGIApp]) -> None:
    """No override: the app's own lazy client points at a closed port, so its first call cannot connect."""
    _, asgi_app = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://test") as client:
        response = await client.get("/tickets/T-1")

    assert (response.status_code, response.json()["code"]) == (503, "dependency_unavailable")
