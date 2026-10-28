from decimal import Decimal

from northdesk.data.repository import DataRepository

REPO = DataRepository.load()


def test_load__reads_every_dataset() -> None:
    assert (len(REPO.customers), len(REPO.invoices), len(REPO.kb), len(REPO.tickets)) == (4, 4, 4, 7)


def test_load__amounts_are_exact_decimals() -> None:
    amounts = {invoice.invoice_id: invoice.amount_eur for invoice in REPO.invoices}

    assert amounts["INV-3001"] == Decimal("990.0")
    assert all(isinstance(amount, Decimal) for amount in amounts.values())


def test_customer__unknown_is_none() -> None:
    assert REPO.customer("C-999") is None


def test_invoices_of__only_that_customer() -> None:
    assert [invoice.invoice_id for invoice in REPO.invoices_of("C-100")] == ["INV-1001", "INV-1002"]
    assert REPO.invoices_of("C-200") == []
