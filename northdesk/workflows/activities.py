"""Activities: everything that touches the outside world or is non-deterministic runs here, not in the workflow."""

from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from northdesk.clients.helpdesk.client import HelpdeskClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.domain.models import ActionResult, Decision, Ticket, TicketContext

UNKNOWN_CUSTOMER = "UnknownCustomer"


@dataclass(frozen=True)
class DecideInput:
    ticket: Ticket
    context: TicketContext


@dataclass(frozen=True)
class ActInput:
    ticket_id: str
    decision: Decision


class TicketActivities:
    def __init__(self, repository: DataRepository, llm: FakeLlm, helpdesk: HelpdeskClient) -> None:
        self._repository = repository
        self._llm = llm
        self._helpdesk = helpdesk

    @activity.defn
    async def load_context(self, customer_id: str) -> TicketContext:
        customer = self._repository.customer(customer_id)
        if customer is None:
            raise ApplicationError("unknown customer", type=UNKNOWN_CUSTOMER, non_retryable=True)
        return TicketContext(customer=customer, invoices=self._repository.invoices_of(customer_id))

    @activity.defn
    async def decide(self, request: DecideInput) -> Decision:
        return await self._llm.decide(request.ticket, request.context)

    @activity.defn
    async def act(self, request: ActInput) -> ActionResult:
        # Same key on every retry of this action, so the helpdesk performs it at most once.
        idempotency_key = f"{activity.info().workflow_id}:{request.decision.action}"
        receipt = await self._helpdesk.perform(
            idempotency_key=idempotency_key,
            ticket_id=request.ticket_id,
            action=request.decision.action,
            details={"message": request.decision.message, "kb_article_ids": request.decision.kb_article_ids},
        )
        return ActionResult(
            action=request.decision.action, helpdesk_action_id=receipt.action_id, idempotency_key=idempotency_key
        )
