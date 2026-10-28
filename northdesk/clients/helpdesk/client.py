"""Client for the helpdesk that carries out actions. It does not retry: the caller (a Temporal activity) does."""

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class HelpdeskReceipt:
    action_id: str
    replayed: bool  # True when the helpdesk had already performed this idempotency key


class HelpdeskClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def perform(
        self, *, idempotency_key: str, ticket_id: str, action: str, details: dict[str, Any]
    ) -> HelpdeskReceipt:
        response = await self._http.post(
            "/actions",
            json={"ticket_id": ticket_id, "action": action, "details": details},
            headers={"Idempotency-Key": idempotency_key},
        )
        response.raise_for_status()
        body = response.json()
        return HelpdeskReceipt(action_id=body["action_id"], replayed=body["replayed"])
