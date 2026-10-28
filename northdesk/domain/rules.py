"""Validation of the agent's decision: pure functions, checked only against data that load_context read.

A decision that breaks any rule becomes an escalation whose reason names the broken rule, so a model that was
talked into something (or simply wrong) can never act on its own.
"""

from dataclasses import dataclass
from decimal import Decimal

from northdesk.domain.models import Action, Category, Command, Decision, Priority, TicketContext


@dataclass(frozen=True)
class Validation:
    command: Command
    violation: str | None  # the broken rule, when the decision was turned into an escalation


def validate(decision: Decision, context: TicketContext) -> Validation:
    violation = find_violation(decision, context)
    if violation is None:
        return Validation(command=_to_command(decision), violation=None)
    escalation = Command(
        action=Action.ESCALATE,
        # Keep the agent's triage when it is valid; it still helps whoever picks up the escalation.
        category=_parse(Category, decision.category) or Category.OTHER,
        priority=_parse(Priority, decision.priority) or Priority.NORMAL,
        reason=f"decision rejected by validation: {violation}",
    )
    return Validation(command=escalation, violation=violation)


def find_violation(decision: Decision, context: TicketContext) -> str | None:
    """The first broken rule, or None when the decision may be carried out as proposed."""
    if _parse(Category, decision.category) is None:
        return f"unknown category {decision.category!r}"
    if _parse(Priority, decision.priority) is None:
        return f"unknown priority {decision.priority!r}"
    match _parse(Action, decision.action):
        case Action.REPLY:
            return _reply_violation(decision, context)
        case Action.REFUND:
            return _refund_violation(decision, context)
        case Action.ESCALATE | Action.CLOSE:
            return None if _has_text(decision.reason) else f"{decision.action} needs a reason"
        case None:
            return f"unknown action {decision.action!r}"


def _reply_violation(decision: Decision, context: TicketContext) -> str | None:
    if not _has_text(decision.message):
        return "reply needs a message"
    unknown = [article_id for article_id in decision.kb_article_ids if article_id not in context.kb_article_ids]
    if unknown:
        return f"reply cites unknown KB articles {unknown}"
    return None


def _refund_violation(decision: Decision, context: TicketContext) -> str | None:
    if not decision.invoice_id:
        return "refund names no invoice"
    customer_id = context.customer.customer_id
    # The context holds only this customer's invoices, so "does not exist" and "belongs to someone else"
    # are one check: either way the invoice is not one this customer can be refunded for.
    invoice = next((inv for inv in context.invoices if inv.invoice_id == decision.invoice_id), None)
    if invoice is None:
        return f"invoice {decision.invoice_id} is not one of customer {customer_id}'s invoices"
    if invoice.status != "paid":
        return f"invoice {invoice.invoice_id} is {invoice.status}, not paid"
    if decision.amount_eur is None:
        return "refund names no amount"
    if not decision.amount_eur.is_finite():  # comparing a Decimal NaN raises instead of returning False
        return f"refund amount {decision.amount_eur} is not a number"
    if not Decimal(0) < decision.amount_eur <= invoice.amount_eur:
        return f"refund of {decision.amount_eur} EUR is outside 0 < amount <= {invoice.amount_eur} EUR"
    return None


def _to_command(decision: Decision) -> Command:
    """Only called once find_violation has passed, so every enum parse succeeds."""
    return Command(
        action=Action(decision.action),
        category=Category(decision.category),
        priority=Priority(decision.priority),
        message=decision.message,
        reason=decision.reason,
        kb_article_ids=list(decision.kb_article_ids),
        invoice_id=decision.invoice_id,
        amount_eur=decision.amount_eur,
    )


def _parse[E: (Action, Category, Priority)](enum: type[E], value: str) -> E | None:
    try:
        return enum(value)
    except ValueError:
        return None


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())
