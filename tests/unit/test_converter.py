from decimal import Decimal

from northdesk.domain.models import Customer, Decision, Invoice, TicketContext
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
