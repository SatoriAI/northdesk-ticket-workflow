import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from temporalio.client import RPCTimeoutOrCancelledError
from temporalio.service import RPCError, RPCStatusCode

from northdesk.api.problems import problem_response
from northdesk.errors import AppError, DependencyUnavailableError

logger = logging.getLogger(__name__)


def _http_code(status: int) -> str:
    return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")


async def _handle_app_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AppError)  # noqa: S101
    is_server_error = exc.status >= HTTPStatus.INTERNAL_SERVER_ERROR
    (logger.error if is_server_error else logger.info)(
        "app error %s on %s", exc.code, request.url.path, exc_info=is_server_error
    )
    return problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        instance=request.url.path,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Never echo request input: keep only loc/msg/type.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    logger.info("validation error on %s", request.url.path)
    return problem_response(
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="validation_error",
        title="Request validation failed",
        instance=request.url.path,
        extensions={"errors": errors},
    )


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    logger.info("http %s on %s", exc.status_code, request.url.path)
    return problem_response(
        status=exc.status_code,
        code=_http_code(exc.status_code),
        title=HTTPStatus(exc.status_code).phrase,
        instance=request.url.path,
        headers=exc.headers,
    )


# RPC statuses that mean nobody answered in time: no worker polling, or Temporal itself unreachable.
_NO_ANSWER = frozenset({RPCStatusCode.CANCELLED, RPCStatusCode.DEADLINE_EXCEEDED, RPCStatusCode.UNAVAILABLE})


def is_temporal_unavailable(exc: BaseException) -> bool:
    """True when Temporal or a worker did not answer: a timed-out or cancelled call, or a lazy client that could not
    connect (which the SDK reports as a plain RuntimeError). Any other error is unexpected and stays a 500."""
    if isinstance(exc, RPCTimeoutOrCancelledError):
        return True
    if isinstance(exc, RPCError):
        return exc.status in _NO_ANSWER
    return isinstance(exc, RuntimeError) and str(exc).startswith("Failed client connect")


async def _handle_temporal_unavailable(request: Request, exc: Exception) -> Response:
    """One place for "Temporal did not answer": every route that talks to Temporal gets the same 503."""
    if not is_temporal_unavailable(exc):
        raise exc  # UnhandledErrorMiddleware logs it and answers 500
    unavailable = DependencyUnavailableError("Temporal or a worker did not answer in time; is the worker running?")
    return await _handle_app_error(request, unavailable)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    # Query and describe time out as RPCError; an Update times out as RPCTimeoutOrCancelledError instead.
    for temporal_error in (RPCTimeoutOrCancelledError, RPCError, RuntimeError):
        app.add_exception_handler(temporal_error, _handle_temporal_unavailable)


class UnhandledErrorMiddleware:
    """Turns unexpected exceptions into a 500 problem *inside* the request-ID middleware, so it keeps the header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("unhandled error on %s", scope.get("path"))
            if response_started:
                raise
            response = problem_response(
                status=AppError.status, code=AppError.code, title=AppError.title, instance=scope.get("path")
            )
            await response(scope, receive, send)
