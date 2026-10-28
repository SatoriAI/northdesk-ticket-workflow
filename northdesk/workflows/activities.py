"""Activities: everything that touches the outside world or is non-deterministic runs here, not in the workflow."""

from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from northdesk.agent.loop import UnparseableDecisionError, run_agent
from northdesk.agent.tools import ToolBox
from northdesk.clients.helpdesk.client import HelpdeskClient, HelpdeskRejectedError
from northdesk.clients.llm.base import LlmClient, LlmRejectedError
from northdesk.data.repository import DataRepository
from northdesk.domain.models import ActionResult, Command, Decision, Ticket, TicketContext

UNKNOWN_CUSTOMER = "UnknownCustomer"
HELPDESK_REJECTED = "HelpdeskRejected"
UNPARSEABLE_DECISION = "UnparseableDecision"
LLM_REJECTED = "LlmRejected"


@dataclass(frozen=True)
class DecideInput:
    ticket: Ticket
    context: TicketContext


@dataclass(frozen=True)
class ActInput:
    ticket_id: str
    command: Command
    idempotency_key: str  # built by the workflow, so it is visible in the workflow history


class TicketActivities:
    def __init__(self, repository: DataRepository, llm: LlmClient, helpdesk: HelpdeskClient) -> None:
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
        # The tools see only what load_context loaded for this ticket's customer.
        tools = ToolBox(context=request.context, kb=self._repository.kb)
        try:
            return await run_agent(self._llm, request.ticket, tools)
        except UnparseableDecisionError as exc:
            # Asking again would replay the same conversation; the workflow escalates instead.
            raise ApplicationError(str(exc), type=UNPARSEABLE_DECISION, non_retryable=True) from exc
        except LlmRejectedError as exc:
            # Bad credentials, a bad request or a refusal will not change on retry; LlmUnavailableError propagates
            # and is retried by DECIDE_RETRY.
            raise ApplicationError(str(exc), type=LLM_REJECTED, non_retryable=True) from exc

    @activity.defn
    async def act(self, request: ActInput) -> ActionResult:
        # Same key on every retry of this action, so the helpdesk performs it at most once.
        command = request.command
        idempotency_key = request.idempotency_key
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
