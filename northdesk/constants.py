"""Static limits and names. Never env-driven."""

# Base URI for RFC 9457 problem `type` values; the error `code` is appended.
ERROR_TYPE_BASE = "https://errors.northdesk.example/"

# One workflow per ticket; the ID doubles as the duplicate guard.
WORKFLOW_ID_PREFIX = "ticket-"


def workflow_id_for(ticket_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{ticket_id}"


def ticket_id_of(workflow_id: str) -> str | None:
    """The ticket a workflow ID belongs to, or None for a workflow that is not a ticket's."""
    return workflow_id.removeprefix(WORKFLOW_ID_PREFIX) if workflow_id.startswith(WORKFLOW_ID_PREFIX) else None


# A query or Update needs a running worker; without one the call would hang, so answer 503 after this.
TEMPORAL_CALL_TIMEOUT_SECONDS = 10.0

# Readiness must answer well inside a probe timeout.
READINESS_TEMPORAL_TIMEOUT_SECONDS = 2.0
