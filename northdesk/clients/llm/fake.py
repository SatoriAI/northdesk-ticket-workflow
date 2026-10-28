"""Fake LLM for running without a key or network. For now it gives one fixed answer (the T-1 reply)."""

from northdesk.domain.models import Decision, Ticket, TicketContext


class FakeLlm:
    async def decide(self, ticket: Ticket, context: TicketContext) -> Decision:
        return Decision(
            category="account",
            priority="normal",
            action="reply",
            message="You can reset your password from the sign-in page: choose 'Forgot password'.",
            kb_article_ids=["KB-1"],
        )
