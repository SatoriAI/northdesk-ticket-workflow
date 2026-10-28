from typing import Any

from northdesk.agent.tools import ToolBox
from northdesk.clients.llm.base import LlmTurn, Message, ToolCall, ToolSpec
from northdesk.data.repository import DataRepository
from northdesk.domain.models import Ticket, TicketContext

REPO = DataRepository.load()


def toolbox_for(ticket: Ticket) -> ToolBox:
    customer = REPO.customer(ticket.customer_id)
    assert customer is not None
    context = TicketContext(
        customer=customer,
        invoices=REPO.invoices_of(ticket.customer_id),
        kb_article_ids=[article.article_id for article in REPO.kb],
    )
    return ToolBox(context=context, kb=REPO.kb)


class ScriptedLlm:
    """Answers with the given turns in order and records every conversation it was shown."""

    def __init__(self, *turns: LlmTurn) -> None:
        self.turns = list(turns)
        self.seen: list[list[Message]] = []

    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn:
        self.seen.append(list(messages))
        return self.turns.pop(0)


def call(name: str, **arguments: Any) -> LlmTurn:
    return LlmTurn(tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=arguments)])
