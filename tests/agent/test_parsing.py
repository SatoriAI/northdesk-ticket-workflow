from decimal import Decimal
from typing import Any

import pytest

from northdesk.agent.parsing import DecisionFormatError, parse_decision

BASE: dict[str, Any] = {"category": "billing", "priority": "normal", "action": "refund", "invoice_id": "INV-1002"}


@pytest.mark.parametrize(("amount", "expected"), [("49.00", "49.00"), (49, "49"), (49.1, "49.1"), (" 10 ", "10")])
def test_parse_decision__amount_becomes_exact_decimal(amount: object, expected: str) -> None:
    assert parse_decision({**BASE, "amount_eur": amount}).amount_eur == Decimal(expected)


@pytest.mark.parametrize("amount", [True, [49], {"eur": 49}, "forty-nine"])
def test_parse_decision__unreadable_amount_is_rejected(amount: object) -> None:
    with pytest.raises(DecisionFormatError, match="amount_eur"):
        parse_decision({**BASE, "amount_eur": amount})


@pytest.mark.parametrize(
    "arguments",
    [
        {**BASE, "category": None},
        {**BASE, "kb_article_ids": [1, 2]},
        {**BASE, "kb_article_ids": "KB-1"},
        {**BASE, "message": "x" * 5000},
        {**BASE, "customer_id": "C-300"},
    ],
)
def test_parse_decision__wrong_shapes_are_rejected(arguments: dict[str, Any]) -> None:
    with pytest.raises(DecisionFormatError):
        parse_decision(arguments)


def test_parse_decision__unknown_enum_value_is_left_to_validation() -> None:
    assert parse_decision({**BASE, "category": "sales"}).category == "sales"
