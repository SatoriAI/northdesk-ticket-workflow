from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

from northdesk.log import request_id_var

PROBLEM_CONTENT_TYPE = "application/problem+json"
# Base URI for RFC 9457 problem `type` values; the error `code` is appended.
ERROR_TYPE_BASE = "https://errors.northdesk.example/"


class ProblemDetails(BaseModel):
    """RFC 9457 body. Extension members (e.g. `errors`) are allowed."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_CONTENT_TYPE


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    instance: str | None,
    detail: str | None = None,
    extensions: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ProblemResponse:
    body: dict[str, Any] = {
        **(extensions or {}),
        "type": f"{ERROR_TYPE_BASE}{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": instance,
        "request_id": request_id_var.get(),
    }
    if detail is not None:
        body["detail"] = detail
    return ProblemResponse(body, status_code=status, headers=headers)


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries so every route documents its problem responses."""
    return {
        status: {
            "description": HTTPStatus(status).phrase,
            "model": ProblemDetails,
            "content": {PROBLEM_CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}},
        }
        for status in statuses
    }
