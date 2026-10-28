"""Domain dataclasses. They cross the Temporal boundary as-is, so they stay plain dataclasses, not Pydantic."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Category(StrEnum):
    ACCOUNT = "account"
    BILLING = "billing"
    TECHNICAL = "technical"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Action(StrEnum):
    REPLY = "reply"
    REFUND = "refund"
    ESCALATE = "escalate"
    CLOSE = "close"


class Step(StrEnum):
    RECEIVED = "received"
    LOADING_CONTEXT = "loading_context"
    DECIDING = "deciding"
    ACTING = "acting"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    customer_id: str
    subject: str
    body: str


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    plan: str
    status: str


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    customer_id: str
    month: str
    amount_eur: Decimal
    status: str


@dataclass(frozen=True)
class KbArticle:
    article_id: str
    title: str
    text: str


@dataclass(frozen=True)
class TicketContext:
    """What load_context read from the data layer: the only facts validation may trust."""

    customer: Customer
    invoices: list[Invoice]


@dataclass(frozen=True)
class Decision:
    """What the agent proposed. Fields are plain strings on purpose: model output is untrusted, so an
    unknown category must reach validation (and escalate) instead of failing to deserialize."""

    category: str
    priority: str
    action: str
    message: str | None = None
    reason: str | None = None
    kb_article_ids: list[str] = field(default_factory=list)
    invoice_id: str | None = None
    amount_eur: Decimal | None = None


@dataclass(frozen=True)
class ActionResult:
    action: str
    helpdesk_action_id: str
    idempotency_key: str


@dataclass(frozen=True)
class HistoryEntry:
    step: Step
    reason: str


@dataclass
class TicketState:
    """Returned by the `state` query: where the ticket is and how it got there."""

    step: Step = Step.RECEIVED
    decision: Decision | None = None
    result: ActionResult | None = None
    history: list[HistoryEntry] = field(default_factory=list)
