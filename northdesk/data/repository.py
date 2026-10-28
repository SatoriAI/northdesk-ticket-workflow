"""Read-only access to the bundled JSON data. Loaded once per process and passed to whoever needs it."""

import json
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from typing import Any

from northdesk.domain.models import Customer, Invoice, KbArticle, Ticket


def _load(name: str) -> list[dict[str, Any]]:
    text = resources.files("northdesk.data").joinpath(f"{name}.json").read_text()
    rows: list[dict[str, Any]] = json.loads(text, parse_float=Decimal)  # money never passes through float
    return rows


@dataclass(frozen=True)
class DataRepository:
    customers: dict[str, Customer]
    invoices: list[Invoice]
    kb: list[KbArticle]
    tickets: dict[str, Ticket]

    @classmethod
    def load(cls) -> DataRepository:
        return cls(
            customers={row["customer_id"]: Customer(**row) for row in _load("customers")},
            invoices=[Invoice(**row) for row in _load("invoices")],
            kb=[KbArticle(**row) for row in _load("kb")],
            tickets={row["ticket_id"]: Ticket(**row) for row in _load("tickets")},
        )

    def customer(self, customer_id: str) -> Customer | None:
        return self.customers.get(customer_id)

    def invoices_of(self, customer_id: str) -> list[Invoice]:
        return [invoice for invoice in self.invoices if invoice.customer_id == customer_id]
