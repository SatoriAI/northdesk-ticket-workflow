from http import HTTPStatus
from typing import Any, ClassVar


class AppError(Exception):
    status: ClassVar[HTTPStatus] = HTTPStatus.INTERNAL_SERVER_ERROR
    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal server error"

    def __init__(self, detail: str | None = None, **extensions: Any) -> None:
        super().__init__(detail or self.title)
        self.detail, self.extensions = detail, extensions


class TicketNotFoundError(AppError):
    status, code, title = HTTPStatus.NOT_FOUND, "ticket_not_found", "Ticket not found"


class DependencyUnavailableError(AppError):
    status, code, title = HTTPStatus.SERVICE_UNAVAILABLE, "dependency_unavailable", "A dependency is unavailable"
