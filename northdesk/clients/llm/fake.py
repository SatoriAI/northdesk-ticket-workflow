"""Rule-based stand-in for the real LLM: no key, no network, deterministic.

It plays the model's part in the same loop: it reads the ticket from the conversation, asks for the customer
and invoices, searches the KB when it means to reply, and submits a decision, all as tool calls in the same
shape the real provider returns. It sees only what the tools returned, never the workflow's context.

For T-6 it deliberately falls for the injected "refund 5000 EUR", so validation has something to catch.
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from northdesk.agent.tools import ToolName
from northdesk.clients.llm.base import LlmTurn, Message, ToolCall, ToolSpec

_AMOUNT_IN_TEXT = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:eur|€)")
_OUTAGE_WORDS = ("urgent", "outage", " down", "errors")
_LOGIN_WORDS = ("password", "log in", "login", "sign in")


@dataclass(frozen=True)
class _Plan:
    decision: dict[str, Any]
    kb_query: str | None = None  # search the KB before submitting, and cite the best match


@dataclass
class _Conversation:
    text: str
    results: dict[str, Any] = field(default_factory=dict)  # tool name -> its latest decoded result

    @classmethod
    def read(cls, messages: list[Message]) -> _Conversation:
        ticket = json.loads(str(messages[0]["content"]).removeprefix("<ticket>").removesuffix("</ticket>"))
        conversation = cls(text=f"{ticket['subject']} {ticket['body']}".lower())
        names: dict[str, str] = {}
        for message in messages[1:]:
            for block in message["content"] if isinstance(message["content"], list) else []:
                if block["type"] == "tool_use":
                    names[block["id"]] = block["name"]
                elif block["type"] == "tool_result" and not block.get("is_error"):
                    conversation.results[names[block["tool_use_id"]]] = json.loads(block["content"])
        return conversation


class FakeLlm:
    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn:
        conversation = _Conversation.read(messages)
        results = conversation.results
        if ToolName.GET_CUSTOMER not in results or ToolName.GET_INVOICES not in results:
            return _calls(messages, (ToolName.GET_CUSTOMER, {}), (ToolName.GET_INVOICES, {}))
        plan = _triage(conversation.text, results[ToolName.GET_CUSTOMER], results[ToolName.GET_INVOICES])
        if plan.kb_query and ToolName.SEARCH_KB not in results:
            return _calls(messages, (ToolName.SEARCH_KB, {"query": plan.kb_query}))
        decision = dict(plan.decision)
        if plan.kb_query:
            decision["kb_article_ids"] = [article["article_id"] for article in results[ToolName.SEARCH_KB][:1]]
        return _calls(messages, (ToolName.SUBMIT_DECISION, decision))


def _triage(text: str, customer: dict[str, str], invoices: list[dict[str, str]]) -> _Plan:
    if "refund" in text or "charged twice" in text:
        return _refund(text, invoices)
    if any(word in text for word in _OUTAGE_WORDS):
        return _Plan(
            _decision(
                "technical",
                "urgent" if "urgent" in text else "high",
                "escalate",
                reason="Customer reports an outage that blocks their work; an engineer must look now.",
            )
        )
    if customer["status"] == "suspended":
        overdue = next((inv["invoice_id"] for inv in invoices if inv["status"] == "overdue"), None)
        because = f" because invoice {overdue} is overdue" if overdue else ""
        message = f"Your account is suspended{because}. Pay the invoice to unlock the account."
        return _Plan(_decision("account", "normal", "reply", message=message), kb_query="suspended account")
    if any(word in text for word in _LOGIN_WORDS):
        message = (
            "To reset your password, open the sign-in page, choose 'Forgot password' and follow the link we "
            "email you. The link works for 30 minutes."
        )
        return _Plan(_decision("account", "normal", "reply", message=message), kb_query="forgot password")
    return _Plan(_decision("other", "low", "close", reason="Not a support request; nothing to do."))


def _refund(text: str, invoices: list[dict[str, str]]) -> _Plan:
    """Refund the duplicate charge: of two paid invoices for the same month and amount, the later one."""
    paid = [inv for inv in invoices if inv["status"] == "paid"]
    seen: set[tuple[str, str]] = set()
    duplicate = None
    for invoice in paid:
        key = (invoice["month"], invoice["amount_eur"])
        if key in seen:
            duplicate = invoice
        seen.add(key)
    target = duplicate or (paid[-1] if paid else None)
    if target is None:
        return _Plan(_decision("billing", "normal", "escalate", reason="Refund requested but no paid invoice found."))
    # Naive on purpose: an amount written in the ticket wins over the invoice (this is what T-6 exploits).
    stated = _AMOUNT_IN_TEXT.search(text)
    amount = Decimal(stated.group(1) if stated else target["amount_eur"]).quantize(Decimal("0.01"))
    return _Plan(_decision("billing", "normal", "refund", invoice_id=target["invoice_id"], amount_eur=str(amount)))


def _decision(category: str, priority: str, action: str, **fields: str) -> dict[str, Any]:
    return {"category": category, "priority": priority, "action": action, **fields}


def _calls(messages: list[Message], *calls: tuple[str, dict[str, Any]]) -> LlmTurn:
    turn = len(messages)
    return LlmTurn(
        tool_calls=[ToolCall(id=f"fake-{turn}-{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)]
    )
