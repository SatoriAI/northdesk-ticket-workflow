"""The workflow on Temporal's time-skipping server, with stub activities: every branch the brief lists."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError, is_cancelled_exception
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.domain.models import (
    Action,
    ActionResult,
    ApprovalRequest,
    Customer,
    Decision,
    Invoice,
    Step,
    Ticket,
    TicketContext,
    TicketState,
)
from northdesk.workflows.activities import UNKNOWN_CUSTOMER, UNPARSEABLE_DECISION, ActInput, DecideInput
from northdesk.workflows.ticket_workflow import ACT_FAILED, REFUND_DECLINED_MESSAGE, TicketWorkflow

TASK_QUEUE = "test-tickets"

# The stub decide answers per ticket, so each test picks the decision the workflow has to handle.
DECISIONS = {
    "T-1": Decision("account", "normal", "reply", message="Use 'Forgot password'.", kb_article_ids=["KB-1"]),
    "T-2": Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=Decimal("49")),
    "T-6": Decision("billing", "urgent", "refund", invoice_id="INV-1002", amount_eur=Decimal("5000")),
    # What Decision(**tool_input) gives when a model writes "amount_eur": 49 as a JSON number.
    "T-int": Decision("billing", "normal", "escalate", reason="check", amount_eur=49),  # type: ignore[arg-type]
}


class Stubs:
    """Stub activities for one test: they record what the workflow asked for and can be told to fail."""

    def __init__(self) -> None:
        self.decided: list[str] = []
        self.performed: list[ActInput] = []
        self.act_failures = 0  # attempts of act that fail before one succeeds
        self.deciding = asyncio.Event()

    @activity.defn(name="load_context")
    async def load_context(self, ticket: Ticket) -> TicketContext:
        if ticket.customer_id == "C-999":
            raise ApplicationError("unknown customer", type=UNKNOWN_CUSTOMER, non_retryable=True)
        return TicketContext(
            customer=Customer(ticket.customer_id, "Northwind Bakery", "business", "active"),
            invoices=[Invoice("INV-1002", ticket.customer_id, "2026-09", Decimal("49.0"), "paid")],
            kb_article_ids=["KB-1"],
        )

    @activity.defn(name="decide")
    async def decide(self, request: DecideInput) -> Decision:
        ticket_id = request.ticket.ticket_id
        self.decided.append(ticket_id)
        if ticket_id == "T-slow":
            self.deciding.set()
            await asyncio.sleep(3600)  # still deciding when the test acts on the workflow
        if ticket_id == "T-unparseable":
            raise ApplicationError("no valid decision after 6 turns", type=UNPARSEABLE_DECISION, non_retryable=True)
        return DECISIONS[ticket_id]

    @activity.defn(name="act")
    async def act(self, request: ActInput) -> ActionResult:
        if activity.info().attempt <= self.act_failures:
            raise RuntimeError("helpdesk unavailable")
        self.performed.append(request)
        action = request.command.action.value
        return ActionResult(action=action, helpdesk_action_id="A-1", idempotency_key=request.idempotency_key)

    @property
    def actions(self) -> list[Action]:
        return [request.command.action for request in self.performed]


@pytest.fixture
async def stubs(env: WorkflowEnvironment) -> AsyncIterator[Stubs]:
    stubs = Stubs()
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[TicketWorkflow],
        activities=[stubs.load_context, stubs.decide, stubs.act],
    ):
        yield stubs


async def start(env: WorkflowEnvironment, ticket_id: str, customer_id: str = "C-100") -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        TicketWorkflow.run,
        Ticket(ticket_id, customer_id, "subject", "body"),
        id=f"ticket-{ticket_id}-{uuid.uuid4().hex[:6]}",
        task_queue=TASK_QUEUE,
        # A workflow task that keeps failing would otherwise retry for ever and hang the test.
        execution_timeout=timedelta(days=3),
    )


async def run(env: WorkflowEnvironment, ticket_id: str, customer_id: str = "C-100") -> TicketState:
    state: TicketState = await (await start(env, ticket_id, customer_id)).result()
    return state


async def wait_for_step(handle: WorkflowHandle[Any, Any], step: Step) -> None:
    for _ in range(200):
        if (await handle.query(TicketWorkflow.state)).step == step:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {step}")


async def awaiting_refund(env: WorkflowEnvironment) -> WorkflowHandle[Any, Any]:
    handle = await start(env, "T-2")
    await wait_for_step(handle, Step.AWAITING_APPROVAL)
    return handle


# --- 1. answered -----------------------------------------------------------------------------------------------


async def test_run__valid_reply_is_carried_out(env: WorkflowEnvironment, stubs: Stubs) -> None:
    state = await run(env, "T-1")

    assert state.step == Step.COMPLETED
    assert stubs.actions == [Action.REPLY]
    key = stubs.performed[0].idempotency_key
    assert key.startswith("ticket-T-1-") and key.endswith(":reply")  # "{workflow_id}:{action}"
    assert [entry.step for entry in state.history] == [
        Step.LOADING_CONTEXT,
        Step.DECIDING,
        Step.VALIDATING,
        Step.ACTING,
        Step.COMPLETED,
    ]


async def test_run__invalid_refund_is_escalated_not_refunded(env: WorkflowEnvironment, stubs: Stubs) -> None:
    state = await run(env, "T-6")

    assert stubs.actions == [Action.ESCALATE]
    acting = next(entry for entry in state.history if entry.step == Step.ACTING)
    assert "outside 0 < amount <= 49.0 EUR" in acting.reason


# --- 2-4. refund approval --------------------------------------------------------------------------------------


async def test_run__valid_refund_waits_without_acting(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await awaiting_refund(env)

    state = await handle.query(TicketWorkflow.state)

    assert state.command is not None and state.command.amount_eur == Decimal("49.00")
    assert stubs.performed == []
    await handle.terminate("test done")


async def test_run__approved_refund_is_carried_out(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await awaiting_refund(env)

    await handle.execute_update(TicketWorkflow.approval, ApprovalRequest("approve", "duplicate charge confirmed"))
    state: TicketState = await handle.result()

    assert stubs.actions == [Action.REFUND]
    assert stubs.performed[0].command.invoice_id == "INV-1002"
    assert state.approval == ApprovalRequest("approve", "duplicate charge confirmed")
    assert state.history[-2].reason == "refund approved: 'duplicate charge confirmed'"


async def test_run__rejected_refund_tells_the_customer(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await awaiting_refund(env)

    await handle.execute_update(TicketWorkflow.approval, ApprovalRequest("reject"))
    state: TicketState = await handle.result()

    assert stubs.actions == [Action.REPLY]
    assert stubs.performed[0].command.message == REFUND_DECLINED_MESSAGE
    assert stubs.performed[0].idempotency_key.endswith(":reply")
    assert state.step == Step.COMPLETED


async def test_run__refund_without_approval_for_24h_escalates(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await awaiting_refund(env)

    await env.sleep(timedelta(hours=24, minutes=1))
    await handle.result()

    assert stubs.actions == [Action.ESCALATE]
    assert stubs.performed[0].command.reason == "refund not approved within 24 h"


async def test_approval__refused_when_not_awaiting_approval(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await start(env, "T-slow")
    await asyncio.wait_for(stubs.deciding.wait(), timeout=30)

    with pytest.raises(WorkflowUpdateFailedError) as raised:
        await handle.execute_update(TicketWorkflow.approval, ApprovalRequest("approve"))

    assert "not awaiting approval" in str(raised.value.cause)
    assert (await handle.query(TicketWorkflow.state)).approval is None
    await handle.terminate("test done")


async def test_approval__second_answer_is_refused(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await awaiting_refund(env)
    await handle.execute_update(TicketWorkflow.approval, ApprovalRequest("reject"))

    with pytest.raises(WorkflowUpdateFailedError):
        await handle.execute_update(TicketWorkflow.approval, ApprovalRequest("approve"))

    await handle.result()
    assert stubs.actions == [Action.REPLY]


@pytest.mark.parametrize("request_", [ApprovalRequest("maybe"), ApprovalRequest("approve", "x" * 501)])
async def test_approval__malformed_answer_is_refused(
    env: WorkflowEnvironment, stubs: Stubs, request_: ApprovalRequest
) -> None:
    handle = await awaiting_refund(env)

    with pytest.raises(WorkflowUpdateFailedError):
        await handle.execute_update(TicketWorkflow.approval, request_)

    assert (await handle.query(TicketWorkflow.state)).step == Step.AWAITING_APPROVAL
    await handle.terminate("test done")


# --- 5. retries ------------------------------------------------------------------------------------------------


async def test_run__act_failure_is_retried_until_it_succeeds(env: WorkflowEnvironment, stubs: Stubs) -> None:
    stubs.act_failures = 2

    state = await run(env, "T-1")

    assert state.step == Step.COMPLETED
    assert stubs.actions == [Action.REPLY]


async def test_run__act_out_of_attempts_fails_with_the_key_to_check(env: WorkflowEnvironment, stubs: Stubs) -> None:
    stubs.act_failures = 100
    handle = await start(env, "T-1")

    with pytest.raises(WorkflowFailureError) as raised:
        await handle.result()

    assert isinstance(raised.value.cause, ApplicationError) and raised.value.cause.type == ACT_FAILED
    state = await handle.query(TicketWorkflow.state)
    assert state.step == Step.FAILED
    assert f"check helpdesk key {handle.id}:reply" in state.history[-1].reason


# --- 6. unknown customer ---------------------------------------------------------------------------------------


async def test_run__unknown_customer_escalates_without_deciding(env: WorkflowEnvironment, stubs: Stubs) -> None:
    state = await run(env, "T-7", customer_id="C-999")

    assert stubs.decided == []
    assert stubs.actions == [Action.ESCALATE]
    assert stubs.performed[0].command.reason == "unknown customer"
    assert [entry.step for entry in state.history] == [Step.LOADING_CONTEXT, Step.ACTING, Step.COMPLETED]


# --- the agent failing -----------------------------------------------------------------------------------------


async def test_run__no_usable_decision_escalates(env: WorkflowEnvironment, stubs: Stubs) -> None:
    state = await run(env, "T-unparseable")

    assert state.decision is None
    assert stubs.actions == [Action.ESCALATE]
    assert stubs.performed[0].command.reason == f"the agent could not decide ({UNPARSEABLE_DECISION})"


async def test_run__numeric_amount_from_model_does_not_stick_the_workflow(
    env: WorkflowEnvironment, stubs: Stubs
) -> None:
    state = await run(env, "T-int")

    assert state.step == Step.COMPLETED
    assert state.decision is not None and state.decision.amount_eur == Decimal("49")


async def test_run__cancel_while_deciding_cancels_without_acting(env: WorkflowEnvironment, stubs: Stubs) -> None:
    handle = await start(env, "T-slow")
    await asyncio.wait_for(stubs.deciding.wait(), timeout=30)

    await handle.cancel()

    with pytest.raises(WorkflowFailureError) as raised:
        await handle.result()
    assert is_cancelled_exception(raised.value.cause)
    assert stubs.performed == []
