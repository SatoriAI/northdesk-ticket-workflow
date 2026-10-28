"""The brief's idempotency test: the helpdesk commits a refund and loses the response; after Temporal retries
the real act activity, exactly one refund exists."""

import uuid
from decimal import Decimal

import httpx
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from northdesk.clients.helpdesk.client import HelpdeskClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.domain.models import Action, ActionResult, Category, Command, Priority
from northdesk.workflows.activities import ActInput, TicketActivities
from northdesk.workflows.policies import ACT_RETRY, ACT_TIMEOUT
from tests.helpdesk.conftest import LOSE_RESPONSE, OK

REFUND = Command(Action.REFUND, Category.BILLING, Priority.NORMAL, invoice_id="INV-1002", amount_eur=Decimal("49.00"))


@workflow.defn(sandboxed=False)
class ActWithRetries:
    """Runs only the act activity, with the production timeout and retry policy."""

    @workflow.run
    async def run(self, request: ActInput) -> ActionResult:
        return await workflow.execute_activity_method(
            TicketActivities.act, request, start_to_close_timeout=ACT_TIMEOUT, retry_policy=ACT_RETRY
        )


async def test_act__lost_response_then_retry_leaves_exactly_one_refund(
    env: WorkflowEnvironment, helpdesk: httpx.AsyncClient, rolls: list[float]
) -> None:
    rolls.extend(LOSE_RESPONSE + OK)  # attempt 1: committed, response lost; attempt 2: answered
    activities = TicketActivities(DataRepository.load(), FakeLlm(), HelpdeskClient(helpdesk))

    async with Worker(
        env.client, task_queue="test-idempotency", workflows=[ActWithRetries], activities=[activities.act]
    ):
        result = await env.client.execute_workflow(
            ActWithRetries.run,
            ActInput(ticket_id="T-2", command=REFUND, idempotency_key="ticket-T-2:refund"),
            id=f"ticket-T-2-{uuid.uuid4().hex[:6]}",
            task_queue="test-idempotency",
        )

    refunds = [action for action in (await helpdesk.get("/actions")).json() if action["action"] == "refund"]
    assert len(refunds) == 1
    assert result.replayed is True  # the retry was answered from the stored result
    assert result.helpdesk_action_id == refunds[0]["action_id"]
    assert refunds[0]["details"]["amount_eur"] == "49.00"
