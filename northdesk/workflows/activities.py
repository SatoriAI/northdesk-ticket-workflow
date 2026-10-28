"""Activities: everything that touches the outside world or is non-deterministic runs here, not in the workflow."""

from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from northdesk.clients.helpdesk.client import HelpdeskClient, HelpdeskRejectedError
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.domain.models import ActionResult, Command, Decision, Ticket, TicketContext

UNKNOWN_CUSTOMER = "UnknownCustomer"
HELPDESK_REJECTED = "HelpdeskRejected"


@dataclass(frozen=True)
class DecideInput:
    ticket: Ticket
    context: TicketContext


@dataclass(frozen=True)
class ActInput:
    ticket_id: str
    command: Command


class TicketActivities:
    def __init__(self, repository: DataRepository, llm: FakeLlm, helpdesk: HelpdeskClient) -> None:
        self._repository = repository
        self._llm = llm
        self._helpdesk = helpdesk

    @activity.defn
    async def load_context(self, ticket: Ticket) -> TicketContext:
        customer = self._repository.customer(ticket.customer_id)
        if customer is None:
            raise ApplicationError("unknown customer", type=UNKNOWN_CUSTOMER, non_retryable=True)
        return TicketContext(
            customer=customer,
            invoices=self._repository.invoices_of(ticket.customer_id),
            kb_article_ids=[article.article_id for article in self._repository.kb],
        )

    @activity.defn
    async def decide(self, request: DecideInput) -> Decision:
        return await self._llm.decide(request.ticket, request.context)

    @activity.defn
    async def act(self, request: ActInput) -> ActionResult:
        # Same key on every retry of this action, so the helpdesk performs it at most once.
        command = request.command
        idempotency_key = f"{activity.info().workflow_id}:{command.action.value}"
        try:
            receipt = await self._helpdesk.perform(
                idempotency_key=idempotency_key, ticket_id=request.ticket_id, command=command
            )
        except HelpdeskRejectedError as exc:
            # A 4xx will not change on retry; HelpdeskUnavailableError propagates and is retried by ACT_RETRY.
            raise ApplicationError(str(exc), type=HELPDESK_REJECTED, non_retryable=True) from exc
        activity.logger.info(
            "helpdesk %s %s for ticket %s",
            "replayed" if receipt.replayed else "performed",
            command.action.value,
            request.ticket_id,
            extra={
                "ticket_id": request.ticket_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": idempotency_key,
                "replayed": receipt.replayed,
            },
        )
        return ActionResult(
            action=command.action.value,
            helpdesk_action_id=receipt.action_id,
            idempotency_key=idempotency_key,
            replayed=receipt.replayed,
        )
