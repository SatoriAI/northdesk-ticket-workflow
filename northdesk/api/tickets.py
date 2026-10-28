import dataclasses
from typing import Annotated

from fastapi import APIRouter, Path, Response, status
from temporalio.service import RPCError, RPCStatusCode

from northdesk.api.deps import SettingsDep, TemporalClientDep
from northdesk.api.problems import problem_responses
from northdesk.api.schemas import TICKET_ID_PATTERN, TicketAccepted, TicketIn, TicketStateOut
from northdesk.domain.models import Ticket
from northdesk.errors import TicketNotFoundError
from northdesk.workflows.starter import start_ticket_workflow, workflow_id_for
from northdesk.workflows.ticket_workflow import TicketWorkflow

router = APIRouter(prefix="/tickets", tags=["tickets"])

TicketIdPath = Annotated[str, Path(pattern=TICKET_ID_PATTERN)]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": TicketAccepted, "description": "Ticket already exists"}, **problem_responses(422)},
)
async def create_ticket(
    ticket: TicketIn, client: TemporalClientDep, settings: SettingsDep, response: Response
) -> TicketAccepted:
    """202 starts the ticket's workflow; 200 means it already exists (running or finished) and nothing was started."""
    started = await start_ticket_workflow(
        client, Ticket(**ticket.model_dump()), task_queue=settings.temporal.task_queue
    )
    if not started:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/tickets/{ticket.ticket_id}"
    return TicketAccepted(ticket_id=ticket.ticket_id, workflow_id=workflow_id_for(ticket.ticket_id))


@router.get("/{ticket_id}", responses=problem_responses(404, 422))
async def get_ticket(ticket_id: TicketIdPath, client: TemporalClientDep) -> TicketStateOut:
    workflow_id = workflow_id_for(ticket_id)
    try:
        state = await client.get_workflow_handle(workflow_id).query(TicketWorkflow.state)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise TicketNotFoundError(f"No ticket {ticket_id}") from exc
        raise
    return TicketStateOut.model_validate(
        {"ticket_id": ticket_id, "workflow_id": workflow_id, **dataclasses.asdict(state)}
    )
