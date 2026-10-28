from decimal import Decimal

import pytest
from temporalio.api.common.v1 import Payload

from northdesk.domain.models import ActionResult, Customer, Decision, Invoice, TicketContext
from northdesk.workflows.converter import DATA_CONVERTER


def test_round_trip__decimal_survives_exactly() -> None:
    converter = DATA_CONVERTER.payload_converter
    context = TicketContext(
        customer=Customer("C-100", "Northwind Bakery", "business", "active"),
        invoices=[Invoice("INV-1002", "C-100", "2026-09", Decimal("49.0"), "paid")],
        kb_article_ids=["KB-1"],
    )

    [payload] = converter.to_payloads([context])

    assert b'"amount_eur":"49.0"' in payload.data
    assert converter.from_payloads([payload], [TicketContext]) == [context]


def test_round_trip__optional_decimal_both_ways() -> None:
    converter = DATA_CONVERTER.payload_converter
    refund = Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur=Decimal("49.00"))
    reply = Decision("account", "normal", "reply", message="hi", kb_article_ids=["KB-1"])

    payloads = converter.to_payloads([refund, reply])

    assert converter.from_payloads(payloads, [Decision, Decision]) == [refund, reply]


@pytest.mark.parametrize(("number", "expected"), [(b"49.1", "49.1"), (b"49", "49"), (b"0.1", "0.1")])
def test_decode__decimal_from_json_number_keeps_its_written_value(number: bytes, expected: str) -> None:
    converter = DATA_CONVERTER.payload_converter
    payload = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"category":"billing","priority":"normal","action":"refund","amount_eur":' + number + b"}",
    )

    [decision] = converter.from_payloads([payload], [Decision])

    assert str(decision.amount_eur) == expected


def test_decode__payload_without_newer_fields_still_decodes() -> None:
    """Workflows started by an older build must stay readable: fields added later need defaults."""
    converter = DATA_CONVERTER.payload_converter
    old_result = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"action":"reply","helpdesk_action_id":"A-1","idempotency_key":"ticket-T-1:reply"}',
    )
    old_context = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"customer":{"customer_id":"C-200","name":"Contoso Labs","plan":"free","status":"active"},'
        b'"invoices":[]}',
    )

    result, context = converter.from_payloads([old_result, old_context], [ActionResult, TicketContext])

    assert result.replayed is False
    assert context.kb_article_ids == []
