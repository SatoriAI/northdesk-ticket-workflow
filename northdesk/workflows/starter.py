"""Starting a ticket's workflow exactly once. Used by the API; kept here because it is Temporal-specific."""

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from northdesk.constants import WORKFLOW_ID_PREFIX
from northdesk.domain.models import Ticket
from northdesk.workflows.ticket_workflow import TicketWorkflow


def workflow_id_for(ticket_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{ticket_id}"


async def start_ticket_workflow(client: Client, ticket: Ticket, *, task_queue: str) -> bool:
    """Start the ticket's workflow unless it already exists. Returns True only if this call started it."""
    try:
        handle = await client.start_workflow(
            TicketWorkflow.run,
            ticket,
            id=workflow_id_for(ticket.ticket_id),
            task_queue=task_queue,
            # A finished ticket is never re-run; a running one is returned instead of failing.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except WorkflowAlreadyStartedError:
        # Only reachable for a closed run: REJECT_DUPLICATE forbids reusing its ID.
        return False
    # The server says whether USE_EXISTING returned a running workflow (started=False). SDK 1.34 exposes
    # that flag only on this private attribute; tests/workflows/test_starter.py pins the behaviour.
    response = handle._start_workflow_response
    return response is not None and response.started
