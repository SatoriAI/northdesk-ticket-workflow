from decimal import Decimal

import pytest

from northdesk.data.repository import DataRepository
from northdesk.domain.models import Action, Category, Decision, Priority, TicketContext
from northdesk.domain.rules import validate

REPO = DataRepository.load()


def context_for(customer_id: str) -> TicketContext:
    customer = REPO.customer(customer_id)
    assert customer is not None
    return TicketContext(
        customer=customer,
        invoices=REPO.invoices_of(customer_id),
        kb_article_ids=[article.article_id for article in REPO.kb],
    )


NORTHWIND = context_for("C-100")  # INV-1001 and INV-1002, both paid 49 EUR
TAILSPIN = context_for("C-400")  # INV-4001, overdue


def reply(**overrides: object) -> Decision:
    fields: dict[str, object] = {
        "category": "account",
        "priority": "normal",
        "action": "reply",
        "message": "Use 'Forgot password' on the sign-in page.",
        "kb_article_ids": ["KB-1"],
    }
    return Decision(**{**fields, **overrides})  # type: ignore[arg-type]


def refund(invoice_id: str | None = "INV-1002", amount: str | None = "49") -> Decision:
    return Decision(
        "billing", "normal", "refund", invoice_id=invoice_id, amount_eur=None if amount is None else Decimal(amount)
    )


def assert_escalated(decision: Decision, context: TicketContext, rule_fragment: str) -> None:
    result = validate(decision, context)
    assert result.command.action == Action.ESCALATE
    assert result.violation is not None and rule_fragment in result.violation
    assert result.command.reason is not None and rule_fragment in result.command.reason


# --- allowed values -------------------------------------------------------------------------------------------


def test_validate__valid_reply_passes_typed() -> None:
    result = validate(reply(), NORTHWIND)

    assert result.violation is None
    assert result.command.action == Action.REPLY
    assert (result.command.category, result.command.priority) == (Category.ACCOUNT, Priority.NORMAL)
    assert result.command.kb_article_ids == ["KB-1"]


def test_validate__unknown_category_escalates_as_other() -> None:
    result = validate(reply(category="sales"), NORTHWIND)

    assert result.violation == "unknown category 'sales'"
    assert (result.command.category, result.command.priority) == (Category.OTHER, Priority.NORMAL)


def test_validate__unknown_priority_escalates() -> None:
    assert_escalated(reply(priority="critical"), NORTHWIND, "unknown priority 'critical'")


def test_validate__unknown_action_escalates() -> None:
    assert_escalated(reply(action="delete_account"), NORTHWIND, "unknown action 'delete_account'")


def test_validate__escalation_keeps_valid_triage() -> None:
    result = validate(refund(amount="5000"), NORTHWIND)

    assert (result.command.category, result.command.priority) == (Category.BILLING, Priority.NORMAL)


# --- reply ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("message", [None, "", "   "])
def test_validate__reply_without_message_escalates(message: str | None) -> None:
    assert_escalated(reply(message=message), NORTHWIND, "reply needs a message")


def test_validate__reply_citing_unknown_kb_escalates() -> None:
    assert_escalated(reply(kb_article_ids=["KB-1", "KB-99"]), NORTHWIND, "unknown KB articles ['KB-99']")


def test_validate__reply_citing_nothing_passes() -> None:
    assert validate(reply(kb_article_ids=[]), NORTHWIND).violation is None


# --- refund ---------------------------------------------------------------------------------------------------


def test_validate__full_refund_of_paid_invoice_passes() -> None:
    result = validate(refund(amount="49.00"), NORTHWIND)

    assert result.violation is None
    assert result.command.action == Action.REFUND
    assert (result.command.invoice_id, result.command.amount_eur) == ("INV-1002", Decimal("49.00"))


def test_validate__partial_refund_passes() -> None:
    assert validate(refund(amount="10.50"), NORTHWIND).violation is None


def test_validate__refund_above_invoice_amount_escalates() -> None:
    """T-6: the injected 5000 EUR refund."""
    assert_escalated(refund(amount="5000"), NORTHWIND, "outside 0 < amount <= 49.0 EUR")


def test_validate__refund_one_cent_above_invoice_escalates() -> None:
    assert_escalated(refund(amount="49.01"), NORTHWIND, "outside 0 < amount")


@pytest.mark.parametrize("amount", ["0", "-49", "0.00"])
def test_validate__refund_of_zero_or_less_escalates(amount: str) -> None:
    assert_escalated(refund(amount=amount), NORTHWIND, "outside 0 < amount")


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity"])
def test_validate__refund_of_non_number_escalates(amount: str) -> None:
    result = validate(refund(amount=amount), NORTHWIND)

    assert result.command.action == Action.ESCALATE


def test_validate__refund_of_someone_elses_invoice_escalates() -> None:
    """INV-3001 exists but belongs to C-300."""
    assert_escalated(refund(invoice_id="INV-3001", amount="49"), NORTHWIND, "not one of customer C-100's invoices")


def test_validate__refund_of_unknown_invoice_escalates() -> None:
    assert_escalated(refund(invoice_id="INV-9999"), NORTHWIND, "not one of customer C-100's invoices")


def test_validate__refund_of_overdue_invoice_escalates() -> None:
    assert_escalated(refund(invoice_id="INV-4001", amount="49"), TAILSPIN, "INV-4001 is overdue, not paid")


def test_validate__refund_without_invoice_escalates() -> None:
    assert_escalated(refund(invoice_id=None), NORTHWIND, "refund names no invoice")


def test_validate__refund_without_amount_escalates() -> None:
    assert_escalated(refund(amount=None), NORTHWIND, "refund names no amount")


# --- escalate and close ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["escalate", "close"])
def test_validate__escalate_or_close_with_reason_passes(action: str) -> None:
    result = validate(Decision("technical", "urgent", action, reason="API outage"), NORTHWIND)

    assert result.violation is None
    assert result.command.action == Action(action)
    assert result.command.reason == "API outage"


@pytest.mark.parametrize("action", ["escalate", "close"])
@pytest.mark.parametrize("reason", [None, "", "  "])
def test_validate__escalate_or_close_without_reason_escalates(action: str, reason: str | None) -> None:
    assert_escalated(Decision("other", "low", action, reason=reason), NORTHWIND, f"{action} needs a reason")
