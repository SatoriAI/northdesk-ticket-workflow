"""Names shared across processes. Never env-driven."""

# One workflow per ticket; the ID doubles as the duplicate guard.
WORKFLOW_ID_PREFIX = "ticket-"


def workflow_id_for(ticket_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{ticket_id}"


def ticket_id_of(workflow_id: str) -> str | None:
    """The ticket a workflow ID belongs to, or None for a workflow that is not a ticket's."""
    return workflow_id.removeprefix(WORKFLOW_ID_PREFIX) if workflow_id.startswith(WORKFLOW_ID_PREFIX) else None
