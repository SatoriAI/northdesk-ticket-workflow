"""Client for the helpdesk that carries out actions. It does not retry: the caller (a Temporal activity) does.

Contract: POST /actions with an Idempotency-Key header. A repeated key returns the original result with
`replayed: true` and performs nothing. 5xx (including a response lost after the action was committed),
timeouts, connection errors, 408, 409 and 429 are temporary; any other 4xx means the request itself is wrong.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from northdesk.domain.models import Command


class HelpdeskUnavailableError(Exception):
    """Temporary: 5xx, timeout or connection error. Retrying with the same key is safe."""


class HelpdeskRejectedError(Exception):
    """The helpdesk refused the request (4xx). Retrying the same request cannot succeed."""


@dataclass(frozen=True)
class HelpdeskReceipt:
    action_id: str
    replayed: bool  # True when the helpdesk had already performed this idempotency key


# 4xx statuses that say "not now" rather than "never": the same request may succeed later. 409 is how an
# idempotency-key server says "a request with this key is still in flight" (IETF Idempotency-Key draft).
_TEMPORARY_CLIENT_ERRORS = frozenset({HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS})


class HelpdeskClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def perform(self, *, idempotency_key: str, ticket_id: str, command: Command) -> HelpdeskReceipt:
        try:
            response = await self._http.post(
                "/actions",
                json={"ticket_id": ticket_id, "action": command.action.value, "details": _details(command)},
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.TransportError as exc:
            raise HelpdeskUnavailableError(f"helpdesk unreachable: {exc!r}") from exc
        if response.is_server_error or response.status_code in _TEMPORARY_CLIENT_ERRORS:
            raise HelpdeskUnavailableError(f"helpdesk returned {response.status_code}")
        if response.is_client_error:
            raise HelpdeskRejectedError(f"helpdesk rejected the request with {response.status_code}")
        body = response.json()
        return HelpdeskReceipt(action_id=body["action_id"], replayed=body["replayed"])


def _details(command: Command) -> dict[str, Any]:
    """Plain JSON: the Command already holds only its action's fields; the amount travels as an exact string."""
    details: dict[str, Any] = {
        "category": command.category.value,
        "priority": command.priority.value,
        "message": command.message,
        "reason": command.reason,
        "kb_article_ids": command.kb_article_ids or None,
        "invoice_id": command.invoice_id,
        "amount_eur": None if command.amount_eur is None else str(command.amount_eur),
    }
    return {key: value for key, value in details.items() if value is not None}
