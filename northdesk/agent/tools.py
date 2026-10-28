"""The agent's tools. All read-only, and bound server-side to the ticket's customer: whatever customer_id the
model passes is ignored, so ticket text cannot steer a tool to another customer's data."""

import json
import re
from dataclasses import dataclass
from typing import Any

from northdesk.clients.llm.base import ToolCall, ToolSpec
from northdesk.domain.models import Action, Category, KbArticle, Priority, TicketContext

GET_CUSTOMER = "get_customer"
GET_INVOICES = "get_invoices"
SEARCH_KB = "search_kb"
SUBMIT_DECISION = "submit_decision"

# Caps keep model text, which ends up in workflow history and the helpdesk, far below Temporal's payload limits.
MAX_MESSAGE_LENGTH = 2000
MAX_REASON_LENGTH = 500
MAX_KB_RESULTS = 3

TOOL_SPECS: list[ToolSpec] = [
    {
        "name": GET_CUSTOMER,
        "description": "The ticket's customer: name, plan and account status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": GET_INVOICES,
        "description": "The ticket's customer's invoices: id, month, amount in EUR and status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": SEARCH_KB,
        "description": "Keyword search over the knowledge base. Returns matching articles with their ids.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 200}},
            "required": ["query"],
        },
    },
    {
        "name": SUBMIT_DECISION,
        "description": "Submit the final decision for this ticket. Call it exactly once, last.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": [c.value for c in Category]},
                "priority": {"type": "string", "enum": [p.value for p in Priority]},
                "action": {"type": "string", "enum": [a.value for a in Action]},
                "message": {
                    "type": "string",
                    "maxLength": MAX_MESSAGE_LENGTH,
                    "description": "For reply: the text sent to the customer.",
                },
                "reason": {
                    "type": "string",
                    "maxLength": MAX_REASON_LENGTH,
                    "description": "For escalate and close: why.",
                },
                "kb_article_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                    "description": "For reply: ids of the KB articles the message relies on.",
                },
                "invoice_id": {"type": "string", "description": "For refund: the invoice to refund."},
                "amount_eur": {
                    "type": "string",
                    "pattern": r"^\d+(\.\d{1,2})?$",
                    "description": 'For refund: the amount in EUR as a decimal string, e.g. "49.00".',
                },
            },
            "required": ["category", "priority", "action"],
            "additionalProperties": False,
        },
    },
]


@dataclass(frozen=True)
class ToolBox:
    context: TicketContext
    kb: list[KbArticle]

    def run(self, call: ToolCall) -> tuple[str, bool]:
        """Run a read-only tool. Returns (JSON content, is_error) for the tool_result block."""
        match call.name:
            case "get_customer":
                result: Any = self.get_customer()
            case "get_invoices":
                result = self.get_invoices()
            case "search_kb":
                query = call.arguments.get("query")
                if not isinstance(query, str):
                    return json.dumps({"error": "search_kb needs a string query"}), True
                result = self.search_kb(query)
            case _:
                return json.dumps({"error": f"unknown tool {call.name[:64]!r}"}), True
        return json.dumps(result), False

    def get_customer(self) -> dict[str, str]:
        customer = self.context.customer
        return {
            "customer_id": customer.customer_id,
            "name": customer.name,
            "plan": customer.plan,
            "status": customer.status,
        }

    def get_invoices(self) -> list[dict[str, str]]:
        return [
            {"invoice_id": inv.invoice_id, "month": inv.month, "amount_eur": str(inv.amount_eur), "status": inv.status}
            for inv in self.context.invoices
        ]

    def search_kb(self, query: str) -> list[dict[str, str]]:
        wanted = _words(query)
        scored = [(len(wanted & _words(f"{a.title} {a.text}")), a) for a in self.kb]
        matches = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])
        return [{"article_id": a.article_id, "title": a.title, "text": a.text} for _, a in matches[:MAX_KB_RESULTS]]


_STOPWORDS = frozenset({"the", "and", "for", "you", "our", "your", "are", "can", "not", "with", "why", "any"})


def _words(text: str) -> set[str]:
    """Lower-case words of three letters or more, with a plural "s" dropped, minus stop words."""
    words = {word.removesuffix("s") for word in re.findall(r"[a-z]{3,}", text.lower())}
    return words - _STOPWORDS
