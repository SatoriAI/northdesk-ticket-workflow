import uuid

from temporalio.testing import WorkflowEnvironment

from northdesk.domain.models import Ticket
from northdesk.workflows.starter import start_ticket_workflow, workflow_id_for

# No worker polls this queue, so a started workflow stays running until the test ends it.
IDLE_QUEUE = "test-starter-idle"


def new_ticket() -> Ticket:
    return Ticket(f"T-{uuid.uuid4().hex[:8]}", "C-200", "subject", "body")


async def test_start__new_ticket_is_started(env: WorkflowEnvironment) -> None:
    assert await start_ticket_workflow(env.client, new_ticket(), task_queue=IDLE_QUEUE) is True


async def test_start__running_duplicate_is_not_started(env: WorkflowEnvironment) -> None:
    ticket = new_ticket()
    await start_ticket_workflow(env.client, ticket, task_queue=IDLE_QUEUE)

    assert await start_ticket_workflow(env.client, ticket, task_queue=IDLE_QUEUE) is False


async def test_start__closed_duplicate_is_not_started(env: WorkflowEnvironment) -> None:
    ticket = new_ticket()
    await start_ticket_workflow(env.client, ticket, task_queue=IDLE_QUEUE)
    await env.client.get_workflow_handle(workflow_id_for(ticket.ticket_id)).terminate("closed for the test")

    assert await start_ticket_workflow(env.client, ticket, task_queue=IDLE_QUEUE) is False
