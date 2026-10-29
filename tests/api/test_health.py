from http import HTTPStatus

import httpx


async def test_live__ok_without_temporal(unreachable_api: httpx.AsyncClient) -> None:
    response = await unreachable_api.get("/health/live")

    assert (response.status_code, response.json()) == (HTTPStatus.OK, {"status": "ok"})


async def test_ready__ok_when_temporal_serves(api_without_worker: httpx.AsyncClient) -> None:
    response = await api_without_worker.get("/health/ready")

    assert (response.status_code, response.json()) == (HTTPStatus.OK, {"status": "ok"})


async def test_ready__problem_503_when_temporal_unreachable(unreachable_api: httpx.AsyncClient) -> None:
    response = await unreachable_api.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "dependency_unavailable"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_unknown_route__problem_404_keeps_the_request_id(unreachable_api: httpx.AsyncClient) -> None:
    response = await unreachable_api.get("/nope", headers={"X-Request-ID": "abc-123"})

    assert (response.status_code, response.json()["code"]) == (HTTPStatus.NOT_FOUND, "not_found")
    assert response.headers["x-request-id"] == "abc-123"
