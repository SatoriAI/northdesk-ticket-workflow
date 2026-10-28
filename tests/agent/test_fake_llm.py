"""The expected outcomes of the sample tickets in fake LLM mode: the fake drives the real loop and tools, then
validation runs."""

from decimal import Decimal

import pytest

from northdesk.agent.loop import run_agent
from northdesk.clients.llm.fake import FakeLlm
from northdesk.domain.models import Action
from northdesk.domain.rules import validate
from tests.support import REPO, toolbox_for


@pytest.mark.parametrize(
    ("ticket_id", "triage", "kb", "validated"),
    [
        ("T-1", ("account", "normal", "reply"), ["KB-1"], Action.REPLY),
        ("T-2", ("billing", "normal", "refund"), [], Action.REFUND),
        ("T-3", ("technical", "urgent", "escalate"), [], Action.ESCALATE),
        ("T-4", ("account", "normal", "reply"), ["KB-4"], Action.REPLY),
        ("T-5", ("other", "low", "close"), [], Action.CLOSE),
        ("T-6", ("billing", "normal", "refund"), [], Action.ESCALATE),
    ],
)
async def test_fake_llm__matches_the_expected_outcome(
    ticket_id: str, triage: tuple[str, str, str], kb: list[str], validated: Action
) -> None:
    ticket = REPO.tickets[ticket_id]
    tools = toolbox_for(ticket)

    decision = await run_agent(FakeLlm(), ticket, tools)
    command = validate(decision, tools.context).command

    assert (decision.category, decision.priority, decision.action) == triage
    assert decision.kb_article_ids == kb
    assert command.action == validated


async def test_fake_llm__t2_refunds_the_duplicate_invoice() -> None:
    decision = await run_agent(FakeLlm(), REPO.tickets["T-2"], toolbox_for(REPO.tickets["T-2"]))

    assert (decision.invoice_id, decision.amount_eur) == ("INV-1002", Decimal("49.00"))


async def test_fake_llm__t6_injection_is_proposed_and_caught_by_validation() -> None:
    ticket = REPO.tickets["T-6"]
    tools = toolbox_for(ticket)

    decision = await run_agent(FakeLlm(), ticket, tools)
    result = validate(decision, tools.context)

    assert decision.amount_eur == Decimal("5000.00")
    assert result.violation is not None and "outside 0 < amount <= 49.0 EUR" in result.violation


async def test_fake_llm__t4_reply_names_the_overdue_invoice() -> None:
    decision = await run_agent(FakeLlm(), REPO.tickets["T-4"], toolbox_for(REPO.tickets["T-4"]))

    assert decision.message is not None and "INV-4001" in decision.message
