"""Composition root. Start with `uvicorn northdesk.main:create_app --factory --log-config northdesk/logging.json`."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypedDict

from fastapi import FastAPI
from temporalio.client import Client

from northdesk import __version__
from northdesk.api.handlers import UnhandledErrorMiddleware, register_exception_handlers
from northdesk.api.health import router as health_router
from northdesk.api.request_id import RequestIdMiddleware
from northdesk.api.tickets import router as tickets_router
from northdesk.settings import ApiSettings
from northdesk.workflows.client import connect


class State(TypedDict):
    settings: ApiSettings
    temporal_client: Client


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[State]:
        async with AsyncExitStack():  # reverse-order cleanup for resources added later, even if startup fails
            # Lazy: the API starts even while Temporal is still booting; /health/ready reports reachability.
            temporal_client = await connect(settings.temporal, lazy=True)
            yield {"settings": settings, "temporal_client": temporal_client}

    app = FastAPI(
        title="Northdesk",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.include_router(health_router)
    app.include_router(tickets_router)
    register_exception_handlers(app)

    # Last added = outermost: unexpected 500s still pass back through the request-ID middleware.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app
