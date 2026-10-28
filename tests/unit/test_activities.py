from decimal import Decimal
from typing import Any

from northdesk.domain.models import Action, Category, Command, Priority
from northdesk.workflows.activities import _helpdesk_details


def test_helpdesk_details__refund_amount_is_exact_string() -> None:
    command = Command(
        Action.REFUND, Category.BILLING, Priority.NORMAL, invoice_id="INV-1002", amount_eur=Decimal("49.00")
    )

    details: dict[str, Any] = _helpdesk_details(command)

    assert details == {"category": "billing", "priority": "normal", "invoice_id": "INV-1002", "amount_eur": "49.00"}


def test_helpdesk_details__escalation_carries_reason() -> None:
    command = Command(Action.ESCALATE, Category.TECHNICAL, Priority.URGENT, reason="API outage")

    assert _helpdesk_details(command)["reason"] == "API outage"
