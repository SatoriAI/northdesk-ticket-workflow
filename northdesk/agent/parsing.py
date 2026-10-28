"""Turning submit_decision arguments into a Decision. Shapes only; the business rules live in domain/rules.py.

Everything that leaves here is well-typed, because the Decision is decoded inside the workflow, where a type
error would not escalate the ticket but fail the workflow task again and again.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from northdesk.agent.tools import (
    MAX_ID_LENGTH,
    MAX_KB_ARTICLE_IDS,
    MAX_MESSAGE_LENGTH,
    MAX_REASON_LENGTH,
    TOOL_SPECS,
    ToolName,
)
from northdesk.domain.models import Decision

_SUBMIT_SCHEMA = next(spec for spec in TOOL_SPECS if spec["name"] == ToolName.SUBMIT_DECISION)["input_schema"]
_FIELDS = frozenset(_SUBMIT_SCHEMA["properties"])


class DecisionFormatError(ValueError):
    """The arguments are not a decision. The message is sent back to the model so it can correct itself."""


def parse_decision(arguments: dict[str, Any]) -> Decision:
    unknown = set(arguments) - _FIELDS
    if unknown:
        raise DecisionFormatError(f"unknown fields: {sorted(unknown)[:5]}")
    return Decision(
        category=_required_text(arguments, "category"),
        priority=_required_text(arguments, "priority"),
        action=_required_text(arguments, "action"),
        message=_optional_text(arguments, "message", MAX_MESSAGE_LENGTH),
        reason=_optional_text(arguments, "reason", MAX_REASON_LENGTH),
        kb_article_ids=_text_list(arguments, "kb_article_ids"),
        invoice_id=_optional_text(arguments, "invoice_id", MAX_ID_LENGTH),
        amount_eur=_amount(arguments.get("amount_eur")),
    )


def _required_text(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise DecisionFormatError(f"{name} must be a string")
    return value[:MAX_ID_LENGTH]  # an unknown value is rejected by validation; it only has to be short


def _optional_text(arguments: dict[str, Any], name: str, max_length: int) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DecisionFormatError(f"{name} must be a string")
    if len(value) > max_length:
        raise DecisionFormatError(f"{name} is longer than {max_length} characters")
    return value


def _text_list(arguments: dict[str, Any], name: str) -> list[str]:
    value = arguments.get(name, [])
    if (
        not isinstance(value, list)
        or len(value) > MAX_KB_ARTICLE_IDS
        or not all(isinstance(item, str) and len(item) <= MAX_ID_LENGTH for item in value)
    ):
        raise DecisionFormatError(f"{name} must be a list of at most {MAX_KB_ARTICLE_IDS} short strings")
    return value


def _amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise DecisionFormatError('amount_eur must be a decimal string such as "49.00"')
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise DecisionFormatError('amount_eur must be a decimal string such as "49.00"') from None
    if not amount.is_finite():  # "NaN" and "Infinity" parse; ask the model again rather than store them
        raise DecisionFormatError('amount_eur must be a finite decimal such as "49.00"')
    return amount
