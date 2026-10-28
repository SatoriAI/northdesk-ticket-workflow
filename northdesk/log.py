"""JSON log lines for every process. Configured at process start from logging.json, never at import."""

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from northdesk.constants import WORKFLOW_ID_PREFIX

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Set by the API once it knows which ticket a request is about; every line logged after that carries it.
ticket_id_var: ContextVar[str | None] = ContextVar("ticket_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=` and is emitted as a field.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def bind_ticket(ticket_id: str) -> None:
    """Mark the rest of this request as being about `ticket_id`, for the logs."""
    ticket_id_var.set(ticket_id)


class ContextLogFilter(logging.Filter):
    """Attach the current request ID and, once known, the ticket and its workflow ID to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        if request_id is not None:
            record.request_id = request_id
        ticket_id = ticket_id_var.get()
        if ticket_id is not None and not hasattr(record, "ticket_id"):
            record.ticket_id = ticket_id
            record.workflow_id = f"{WORKFLOW_ID_PREFIX}{ticket_id}"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line.update({key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS})
        if record.exc_info:
            line["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging() -> None:
    """For entrypoints that are not started by uvicorn (uvicorn reads the same file via --log-config)."""
    config = json.loads(resources.files("northdesk").joinpath("logging.json").read_text())
    logging.config.dictConfig(config)
