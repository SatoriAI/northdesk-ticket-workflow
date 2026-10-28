import dataclasses
from typing import Annotated

from fastapi import APIRouter, Path, Response, status
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from northdesk.api.deps import SettingsDep, TemporalClientDep
from northdesk.api.problems import problem_responses
from northdesk.api.schemas import TICKET_ID_PATTERN, TicketAccepted, TicketIn, TicketStateOut
from northdesk.constants import WORKFLOW_ID_PREFIX
from northdesk.domain.models import Ticket
from northdesk.errors import TicketNotFoundError
from northdesk.workflows.ticket_workflow import TicketWorkflow

router = APIRouter(prefix="/tickets", tags=["tickets"])

TicketIdPath = Annotated[str, Path(pattern=TICKET_ID_PATTERN)]


def _workflow_id(ticket_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{ticket_id}"


@router.post("", status_code=status.HTTP_202_ACCEPTED, responses=problem_responses(422))
async def create_ticket(
    ticket: TicketIn, client: TemporalClientDep, settings: SettingsDep, response: Response
) -> TicketAccepted:
    workflow_id = _workflow_id(ticket.ticket_id)
    await client.start_workflow(
        TicketWorkflow.run,
        Ticket(**ticket.model_dump()),
        id=workflow_id,
        task_queue=settings.temporal.task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    response.headers["Location"] = f"/tickets/{ticket.ticket_id}"
    return TicketAccepted(ticket_id=ticket.ticket_id, workflow_id=workflow_id)


@router.get("/{ticket_id}", responses=problem_responses(404, 422))
async def get_ticket(ticket_id: TicketIdPath, client: TemporalClientDep) -> TicketStateOut:
    workflow_id = _workflow_id(ticket_id)
    try:
        state = await client.get_workflow_handle(workflow_id).query(TicketWorkflow.state)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise TicketNotFoundError(f"No ticket {ticket_id}") from exc
        raise
    return TicketStateOut.model_validate(
        {"ticket_id": ticket_id, "workflow_id": workflow_id, **dataclasses.asdict(state)}
    )
