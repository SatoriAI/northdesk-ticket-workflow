"""Validation of the agent's decision: pure functions, checked only against data that load_context read.

A decision that breaks any rule becomes an escalation whose reason names the broken rule, so a model that was
talked into something (or simply wrong) can never act on its own. A decision that passes becomes a Command that
carries only the fields its action's rules checked; everything else the model wrote is dropped.
"""

from dataclasses import dataclass
from decimal import Decimal

from northdesk.domain.models import Action, Category, Command, Decision, Priority, TicketContext

CENT = Decimal("0.01")
# Model text quoted in a violation goes into history, logs and the helpdesk; keep it short.
MAX_QUOTED_LENGTH = 64


@dataclass(frozen=True)
class Validation:
    command: Command
    violation: str | None  # the broken rule, when the decision was turned into an escalation


def validate(decision: Decision, context: TicketContext) -> Validation:
    checked = _check(decision, context)
    if isinstance(checked, Command):
        return Validation(command=checked, violation=None)
    escalation = Command(
        action=Action.ESCALATE,
        # Keep the agent's triage when it is valid; it still helps whoever picks up the escalation.
        category=_parse(Category, decision.category) or Category.OTHER,
        priority=_parse(Priority, decision.priority) or Priority.NORMAL,
        reason=f"decision rejected by validation: {checked}",
    )
    return Validation(command=escalation, violation=checked)


def _check(decision: Decision, context: TicketContext) -> Command | str:
    """The Command to carry out, or the first broken rule."""
    category = _parse(Category, decision.category)
    if category is None:
        return f"unknown category {_quote(decision.category)}"
    priority = _parse(Priority, decision.priority)
    if priority is None:
        return f"unknown priority {_quote(decision.priority)}"
    match _parse(Action, decision.action):
        case Action.REPLY:
            return _check_reply(decision, context, category, priority)
        case Action.REFUND:
            return _check_refund(decision, context, category, priority)
        case Action.ESCALATE | Action.CLOSE as action:
            if not _has_text(decision.reason):
                return f"{action} needs a reason"
            return Command(action, category, priority, reason=decision.reason)
        case None:
            return f"unknown action {_quote(decision.action)}"


def _check_reply(decision: Decision, context: TicketContext, category: Category, priority: Priority) -> Command | str:
    if not _has_text(decision.message):
        return "reply needs a message"
    unknown = [article_id for article_id in decision.kb_article_ids if article_id not in context.kb_article_ids]
    if unknown:
        return f"reply cites unknown KB articles {_quote(', '.join(unknown))}"
    return Command(
        Action.REPLY, category, priority, message=decision.message, kb_article_ids=list(decision.kb_article_ids)
    )


def _check_refund(decision: Decision, context: TicketContext, category: Category, priority: Priority) -> Command | str:
    if not decision.invoice_id:
        return "refund names no invoice"
    customer_id = context.customer.customer_id
    invoice = next((inv for inv in context.invoices if inv.invoice_id == decision.invoice_id), None)
    if invoice is None:
        return f"invoice {_quote(decision.invoice_id)} is not among the loaded invoices"
    # load_context loads only this customer's invoices; checked here too so the rule does not rest on that.
    if invoice.customer_id != customer_id:
        return f"invoice {invoice.invoice_id} belongs to {invoice.customer_id}, not {customer_id}"
    if invoice.status != "paid":
        return f"invoice {invoice.invoice_id} is {invoice.status}, not paid"
    amount = decision.amount_eur
    if amount is None:
        return "refund names no amount"
    if not amount.is_finite():  # comparing a Decimal NaN raises instead of returning False
        return f"refund amount {_quote(str(amount))} is not a number"
    if not Decimal(0) < amount <= invoice.amount_eur:
        return f"refund of {_quote(str(amount))} EUR is outside 0 < amount <= {invoice.amount_eur.quantize(CENT)} EUR"
    if amount != amount.quantize(CENT):
        return f"refund of {_quote(str(amount))} EUR has more than two decimal places"
    return Command(Action.REFUND, category, priority, invoice_id=invoice.invoice_id, amount_eur=amount.quantize(CENT))


def _parse[E: (Action, Category, Priority)](enum: type[E], value: str) -> E | None:
    try:
        return enum(value)
    except ValueError:
        return None


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _quote(value: str) -> str:
    return repr(value if len(value) <= MAX_QUOTED_LENGTH else value[: MAX_QUOTED_LENGTH - 3] + "...")
