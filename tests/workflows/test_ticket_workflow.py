import uuid
from decimal import Decimal

from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.domain.models import (
    Action,
    ActionResult,
    Customer,
    Decision,
    Invoice,
    Step,
    Ticket,
    TicketContext,
    TicketState,
)
from northdesk.workflows.activities import ActInput, DecideInput
from northdesk.workflows.ticket_workflow import TicketWorkflow

TASK_QUEUE = "test-tickets"

# The stub decide answers per ticket, so each test picks the decision the workflow has to handle.
DECISIONS = {
    "T-1": Decision("account", "normal", "reply", message="Use 'Forgot password'.", kb_article_ids=["KB-1"]),
    "T-6": Decision("billing", "urgent", "refund", invoice_id="INV-1002", amount_eur=Decimal("5000")),
}
performed: list[ActInput] = []


@activity.defn(name="load_context")
async def load_context(ticket: Ticket) -> TicketContext:
    return TicketContext(
        customer=Customer(ticket.customer_id, "Northwind Bakery", "business", "active"),
        invoices=[Invoice("INV-1002", ticket.customer_id, "2026-09", Decimal("49.0"), "paid")],
        kb_article_ids=["KB-1"],
    )


@activity.defn(name="decide")
async def decide(request: DecideInput) -> Decision:
    return DECISIONS[request.ticket.ticket_id]


@activity.defn(name="act")
async def act(request: ActInput) -> ActionResult:
    performed.append(request)
    action = request.command.action.value
    return ActionResult(action=action, helpdesk_action_id="A-1", idempotency_key=f"key:{action}")


async def run_ticket(client: Client, ticket_id: str) -> TicketState:
    ticket = Ticket(ticket_id, "C-100", "subject", "body")
    async with Worker(
        client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=[load_context, decide, act]
    ):
        return await client.execute_workflow(
            TicketWorkflow.run, ticket, id=f"ticket-{ticket_id}-{uuid.uuid4().hex[:6]}", task_queue=TASK_QUEUE
        )


async def test_run__valid_reply_is_carried_out(env: WorkflowEnvironment) -> None:
    state = await run_ticket(env.client, "T-1")

    assert state.step == Step.COMPLETED
    assert state.command is not None and state.command.action == Action.REPLY
    assert state.result is not None and state.result.action == "reply"
    assert [entry.step for entry in state.history] == [
        Step.LOADING_CONTEXT,
        Step.DECIDING,
        Step.VALIDATING,
        Step.ACTING,
        Step.COMPLETED,
    ]


async def test_run__invalid_refund_is_escalated_not_refunded(env: WorkflowEnvironment) -> None:
    performed.clear()

    state = await run_ticket(env.client, "T-6")

    assert state.decision is not None and state.decision.action == "refund"
    assert state.command is not None and state.command.action == Action.ESCALATE
    assert [request.command.action for request in performed] == [Action.ESCALATE]
    acting = next(entry for entry in state.history if entry.step == Step.ACTING)
    assert "outside 0 < amount <= 49.0 EUR" in acting.reason
