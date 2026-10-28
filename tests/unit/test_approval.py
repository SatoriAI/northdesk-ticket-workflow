from northdesk.domain.approval import RefusalKind, approval_refusal
from northdesk.domain.models import ApprovalRequest, Step, TicketState

WAITING = TicketState(step=Step.AWAITING_APPROVAL)


def test_approval_refusal__waiting_ticket_accepts_an_answer() -> None:
    assert approval_refusal(WAITING, ApprovalRequest("approve")) is None


def test_approval_refusal__second_answer_says_it_was_answered() -> None:
    """The loser of an approve and a reject sent at once sees why, not "not awaiting (step: awaiting_approval)"."""
    answered = TicketState(step=Step.AWAITING_APPROVAL, approval=ApprovalRequest("approve"))

    refusal = approval_refusal(answered, ApprovalRequest("reject"))

    assert refusal is not None
    assert (refusal.kind, refusal.reason) == (RefusalKind.NOT_AWAITING, "ticket was already answered: approve")


def test_approval_refusal__other_step_names_the_step() -> None:
    refusal = approval_refusal(TicketState(step=Step.DECIDING), ApprovalRequest("approve"))

    assert refusal is not None and refusal.reason == "ticket is not awaiting approval (step: deciding)"


def test_approval_refusal__malformed_answer_is_invalid() -> None:
    refusal = approval_refusal(WAITING, ApprovalRequest("maybe"))

    assert refusal is not None and refusal.kind == RefusalKind.INVALID
