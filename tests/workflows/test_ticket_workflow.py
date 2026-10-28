import uuid

from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.domain.models import ActionResult, Customer, Decision, Step, Ticket, TicketContext, TicketState
from northdesk.workflows.activities import ActInput, DecideInput
from northdesk.workflows.ticket_workflow import TicketWorkflow

TASK_QUEUE = "test-tickets"
T1 = Ticket("T-1", "C-200", "Can't log in", "I forgot my password and can't get into my account.")


@activity.defn(name="load_context")
async def load_context(customer_id: str) -> TicketContext:
    return TicketContext(customer=Customer(customer_id, "Contoso Labs", "free", "active"), invoices=[])


@activity.defn(name="decide")
async def decide(request: DecideInput) -> Decision:
    return Decision("account", "normal", "reply", message="Use 'Forgot password'.", kb_article_ids=["KB-1"])


@activity.defn(name="act")
async def act(request: ActInput) -> ActionResult:
    key = f"{activity.info().workflow_id}:{request.decision.action}"
    return ActionResult(action=request.decision.action, helpdesk_action_id="A-1", idempotency_key=key)


async def run_ticket(client: Client, ticket: Ticket) -> TicketState:
    async with Worker(
        client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=[load_context, decide, act]
    ):
        return await client.execute_workflow(
            TicketWorkflow.run, ticket, id=f"ticket-{ticket.ticket_id}-{uuid.uuid4().hex[:6]}", task_queue=TASK_QUEUE
        )


async def test_run__reply_completes(env: WorkflowEnvironment) -> None:
    state = await run_ticket(env.client, T1)

    assert state.step == Step.COMPLETED
    assert state.decision is not None and state.decision.kb_article_ids == ["KB-1"]
    assert state.result is not None and state.result.idempotency_key.endswith(":reply")
    assert [entry.step for entry in state.history] == [Step.LOADING_CONTEXT, Step.DECIDING, Step.ACTING, Step.COMPLETED]
