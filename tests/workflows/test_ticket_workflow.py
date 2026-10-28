import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
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
    "T-2": Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=Decimal("49")),
    "T-6": Decision("billing", "urgent", "refund", invoice_id="INV-1002", amount_eur=Decimal("5000")),
    # What Decision(**tool_input) gives when a model writes "amount_eur": 49 as a JSON number.
    "T-2-int": Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=49),  # type: ignore[arg-type]
}


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


class ActRecorder:
    """A stub act activity that records what it was asked to carry out."""

    def __init__(self) -> None:
        self.performed: list[ActInput] = []

    @activity.defn(name="act")
    async def act(self, request: ActInput) -> ActionResult:
        self.performed.append(request)
        action = request.command.action.value
        return ActionResult(action=action, helpdesk_action_id="A-1", idempotency_key=f"key:{action}", replayed=False)


@pytest.fixture
def recorder() -> ActRecorder:
    return ActRecorder()


async def run_ticket(client: Client, ticket_id: str, recorder: ActRecorder) -> TicketState:
    ticket = Ticket(ticket_id, "C-100", "subject", "body")
    async with Worker(
        client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=[load_context, decide, recorder.act]
    ):
        return await client.execute_workflow(
            TicketWorkflow.run,
            ticket,
            id=f"ticket-{ticket_id}-{uuid.uuid4().hex[:6]}",
            task_queue=TASK_QUEUE,
            # A workflow task that keeps failing would otherwise retry for ever and hang the test.
            execution_timeout=timedelta(minutes=5),
        )


async def test_run__valid_reply_is_carried_out(env: WorkflowEnvironment, recorder: ActRecorder) -> None:
    state = await run_ticket(env.client, "T-1", recorder)

    assert state.step == Step.COMPLETED
    assert [request.command.action for request in recorder.performed] == [Action.REPLY]
    assert [entry.step for entry in state.history] == [
        Step.LOADING_CONTEXT,
        Step.DECIDING,
        Step.VALIDATING,
        Step.ACTING,
        Step.COMPLETED,
    ]


async def test_run__invalid_refund_is_escalated_not_refunded(env: WorkflowEnvironment, recorder: ActRecorder) -> None:
    state = await run_ticket(env.client, "T-6", recorder)

    assert state.decision is not None and state.decision.action == "refund"
    assert [request.command.action for request in recorder.performed] == [Action.ESCALATE]
    acting = next(entry for entry in state.history if entry.step == Step.ACTING)
    assert "outside 0 < amount <= 49.0 EUR" in acting.reason


async def test_run__valid_refund_never_reaches_act_unapproved(env: WorkflowEnvironment, recorder: ActRecorder) -> None:
    state = await run_ticket(env.client, "T-2", recorder)

    assert Action.REFUND not in [request.command.action for request in recorder.performed]
    assert state.command is not None and state.command.action == Action.ESCALATE


async def test_run__numeric_amount_from_model_does_not_stick_the_workflow(
    env: WorkflowEnvironment, recorder: ActRecorder
) -> None:
    state = await run_ticket(env.client, "T-2-int", recorder)

    assert state.step == Step.COMPLETED
    assert state.decision is not None and state.decision.amount_eur == Decimal("49")
