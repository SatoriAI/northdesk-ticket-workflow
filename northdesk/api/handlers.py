import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from northdesk.api.problems import problem_response
from northdesk.errors import AppError

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
        extensions=exc.extensions,
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


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)


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
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                code="internal_error",
                title="Internal server error",
                instance=scope.get("path"),
            )
            await response(scope, receive, send)
