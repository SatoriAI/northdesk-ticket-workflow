import dataclasses
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status
from temporalio.client import RPCTimeoutOrCancelledError, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from northdesk.api.deps import SettingsDep, TemporalClientDep
from northdesk.api.problems import problem_responses
from northdesk.api.schemas import TICKET_ID_PATTERN, ApprovalIn, TicketAccepted, TicketIn, TicketStateOut
from northdesk.constants import workflow_id_for
from northdesk.domain.models import ApprovalRequest, Ticket, TicketState
from northdesk.errors import DependencyUnavailableError, TicketNotAwaitingApprovalError, TicketNotFoundError
from northdesk.log import bind_ticket
from northdesk.workflows.contract import APPROVAL_UPDATE, NOT_AWAITING_APPROVAL, STATE_QUERY
from northdesk.workflows.starter import start_ticket_workflow

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _bound_ticket_id(ticket_id: Annotated[str, Path(pattern=TICKET_ID_PATTERN)]) -> str:
    """The validated path ticket ID, also bound to this request's log lines. Async, so it shares their context."""
    bind_ticket(ticket_id)
    return ticket_id


TicketIdPath = Annotated[str, Depends(_bound_ticket_id)]
# A query or Update needs a running worker; without one the call would hang, so give up (503) after this.
CALL_TIMEOUT = timedelta(seconds=10)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a ticket",
    responses={
        202: {
            "description": "Ticket accepted; its workflow has started",
            "headers": {
                "Location": {"description": "The ticket's URL, e.g. /tickets/T-1", "schema": {"type": "string"}}
            },
        },
        200: {
            "model": TicketAccepted,
            "description": "The ticket already exists (running or finished); the new body is ignored, nothing starts",
        },
        **problem_responses(422, 503),
    },
)
async def create_ticket(
    ticket: TicketIn, client: TemporalClientDep, settings: SettingsDep, response: Response
) -> TicketAccepted:
    """202 starts the ticket's workflow; 200 means it already exists (running or finished) and nothing was started."""
    bind_ticket(ticket.ticket_id)
    started = await start_ticket_workflow(
        client, Ticket(**ticket.model_dump()), task_queue=settings.temporal.task_queue
    )
    if not started:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/tickets/{ticket.ticket_id}"
    return TicketAccepted(ticket_id=ticket.ticket_id, workflow_id=workflow_id_for(ticket.ticket_id))


@router.get("/{ticket_id}", summary="Read a ticket's state", responses=problem_responses(404, 422, 503))
async def get_ticket(ticket_id: TicketIdPath, client: TemporalClientDep) -> TicketStateOut:
    """The ticket's current step, decision, result and history, from the workflow's `state` query."""
    handle = client.get_workflow_handle(workflow_id_for(ticket_id))
    try:
        state = await handle.query(STATE_QUERY, result_type=TicketState, rpc_timeout=CALL_TIMEOUT)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise TicketNotFoundError(f"No ticket {ticket_id}") from exc
        raise  # nobody answering is a 503, mapped once in api/handlers.py
    return _state_out(ticket_id, handle.id, state)


@router.post(
    "/{ticket_id}/approval", summary="Approve or reject a refund", responses=problem_responses(404, 409, 422, 503)
)
async def approve_ticket(ticket_id: TicketIdPath, approval: ApprovalIn, client: TemporalClientDep) -> TicketStateOut:
    """Approve or reject the refund the ticket is waiting on (the workflow's `approval` Update).

    The returned state is the moment the answer was accepted: `approval` is set and the step is still
    `awaiting_approval`; the workflow moves on to acting right after (see GET). 409 when the ticket is not
    waiting for approval: at another step, already answered, or finished.
    """
    handle = client.get_workflow_handle(workflow_id_for(ticket_id))
    request = ApprovalRequest(approval.decision, approval.comment)
    try:
        state = await handle.execute_update(APPROVAL_UPDATE, request, result_type=TicketState, rpc_timeout=CALL_TIMEOUT)
    except RPCTimeoutOrCancelledError as exc:
        # Unlike the other routes, the request may still take effect: the Update waits for a worker to pick it up.
        raise DependencyUnavailableError(
            "no worker answered in time; the approval may still be applied when one polls, so check GET before "
            "resending it"
        ) from exc
    except WorkflowUpdateFailedError as exc:
        cause = exc.cause
        if isinstance(cause, ApplicationError) and cause.type == NOT_AWAITING_APPROVAL:
            raise TicketNotAwaitingApprovalError(cause.message) from exc
        raise  # InvalidApproval cannot get past ApprovalIn; anything else is a bug and a 500
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        # Temporal answers NOT_FOUND both for an unknown workflow and for one that has already finished.
        if await _exists(handle):
            raise TicketNotAwaitingApprovalError("ticket has already finished") from exc
        raise TicketNotFoundError(f"No ticket {ticket_id}") from exc
    return _state_out(ticket_id, handle.id, state)


async def _exists(handle: WorkflowHandle[Any, Any]) -> bool:
    try:
        await handle.describe(rpc_timeout=CALL_TIMEOUT)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return True


def _state_out(ticket_id: str, workflow_id: str, state: TicketState) -> TicketStateOut:
    return TicketStateOut.model_validate(
        {"ticket_id": ticket_id, "workflow_id": workflow_id, **dataclasses.asdict(state)}
    )
