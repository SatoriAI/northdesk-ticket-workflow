from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from northdesk.log import request_id_var

PROBLEM_CONTENT_TYPE = "application/problem+json"
# Base URI for RFC 9457 problem `type` values; the error `code` is appended.
ERROR_TYPE_BASE = "https://errors.northdesk.example/"


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ProblemDetails(BaseModel):
    """RFC 9457 body. `code` is the stable contract; titles and details may change."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "type": "https://errors.northdesk.example/ticket_not_awaiting_approval",
                    "title": "Ticket is not awaiting approval",
                    "status": 409,
                    "code": "ticket_not_awaiting_approval",
                    "detail": "ticket is not awaiting approval (step: deciding)",
                    "instance": "/tickets/T-1/approval",
                    "request_id": "6f1c2a9e0b7d4c1e",
                }
            ]
        },
    )

    type: str
    title: str
    status: int
    code: str = Field(
        description="validation_error, ticket_not_found, ticket_not_awaiting_approval, dependency_unavailable, "
        "internal_error, not_found or method_not_allowed"
    )
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[ValidationIssue] | None = Field(default=None, description="Only on validation_error (422)")


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


_DESCRIPTIONS = {
    503: "Temporal or a worker did not answer in time (GET and approval need a running worker); an approval may "
    "still be applied once one polls, so check GET before resending it",
}


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries so every route documents its problem responses."""
    return {
        status: {
            "description": _DESCRIPTIONS.get(status, HTTPStatus(status).phrase),
            "model": ProblemDetails,
            "content": {PROBLEM_CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}},
        }
        for status in statuses
    }
