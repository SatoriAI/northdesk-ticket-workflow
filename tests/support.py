"""Fakes and sample data shared by the tests. Fixtures live in the conftest files; everything else lives here."""

import asyncio
from decimal import Decimal
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from northdesk.agent.tools import ToolBox
from northdesk.clients.llm.base import LlmTurn, Message, ToolCall, ToolSpec
from northdesk.data.repository import DataRepository
from northdesk.domain.models import (
    Action,
    ActionResult,
    Category,
    Command,
    Customer,
    Decision,
    Invoice,
    Priority,
    Ticket,
    TicketContext,
)
from northdesk.workflows.activities import (
    HELPDESK_REJECTED,
    UNKNOWN_CUSTOMER,
    UNPARSEABLE_DECISION,
    ActInput,
    DecideInput,
)

REPO = DataRepository.load()
REFUND = Command(Action.REFUND, Category.BILLING, Priority.NORMAL, invoice_id="INV-1002", amount_eur=Decimal("49.00"))


def context_for(customer_id: str) -> TicketContext:
    context = REPO.context_for(customer_id)
    assert context is not None, customer_id
    return context


def toolbox_for(ticket: Ticket) -> ToolBox:
    return ToolBox(context=context_for(ticket.customer_id), kb=REPO.kb)


class ScriptedLlm:
    """An LlmClient that answers with the given turns in order and records every conversation it was shown."""

    def __init__(self, *turns: LlmTurn) -> None:
        self.turns = list(turns)
        self.seen: list[list[Message]] = []

    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn:
        self.seen.append(list(messages))
        return self.turns.pop(0)


def call(name: str, **arguments: Any) -> LlmTurn:
    return LlmTurn(tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=arguments)])


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
        self.reject_act = False  # act fails as a helpdesk 4xx would: non-retryable
        self.act_attempts = 0
        self.deciding = asyncio.Event()

    @activity.defn(name="load_context")
    async def load_context(self, ticket: Ticket) -> TicketContext:
        if ticket.customer_id == "C-broken":
            raise RuntimeError("data layer unavailable")
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
        self.act_attempts += 1
        if self.reject_act:
            raise ApplicationError("helpdesk rejected the request with 422", type=HELPDESK_REJECTED, non_retryable=True)
        if activity.info().attempt <= self.act_failures:
            raise RuntimeError("helpdesk unavailable")
        self.performed.append(request)
        action = request.command.action.value
        return ActionResult(action=action, helpdesk_action_id="A-1", idempotency_key=request.idempotency_key)

    @property
    def actions(self) -> list[Action]:
        return [request.command.action for request in self.performed]
