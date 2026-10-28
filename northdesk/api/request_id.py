import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from northdesk.log import request_id_var, ticket_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            # Only reuse well-formed IDs so arbitrary input is never reflected into logs or headers.
            return candidate if _SAFE_REQUEST_ID.match(candidate) else None
    return None


# Replaces uvicorn's access log, which is written outside the request and so cannot carry its IDs.
access_logger = logging.getLogger("northdesk.access")


class RequestIdMiddleware:
    """Gives every request an ID (accepted from X-Request-ID when well formed) and writes its access line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex[:16]
        request_token = request_id_var.set(request_id)
        ticket_token = ticket_id_var.set(None)  # bound by the route once it knows the ticket
        started = time.perf_counter()
        status = 500  # if the app fails before answering

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            # Still inside the request's context, so the line carries request_id and any bound ticket_id.
            access_logger.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status,
                extra={"status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 1)},
            )
            ticket_id_var.reset(ticket_token)
            request_id_var.reset(request_token)
