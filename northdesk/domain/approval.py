"""Whether a person's answer to a refund can be accepted: a pure rule, like the decision rules."""

from dataclasses import dataclass
from enum import StrEnum

from northdesk.domain.models import ApprovalDecision, ApprovalRequest, Step, TicketState

MAX_APPROVAL_COMMENT_LENGTH = 500


class RefusalKind(StrEnum):
    NOT_AWAITING = "not_awaiting"  # nothing to answer right now: a conflict with the ticket's state
    INVALID = "invalid"  # the answer itself is malformed


@dataclass(frozen=True)
class ApprovalRefusal:
    kind: RefusalKind
    reason: str


def approval_refusal(state: TicketState, request: ApprovalRequest) -> ApprovalRefusal | None:
    """Why this answer cannot be accepted now, or None when it can."""
    if state.approval is not None:  # e.g. the loser of an approve and a reject sent at the same moment
        return ApprovalRefusal(RefusalKind.NOT_AWAITING, f"ticket was already answered: {state.approval.decision}")
    if state.step != Step.AWAITING_APPROVAL:
        return ApprovalRefusal(RefusalKind.NOT_AWAITING, f"ticket is not awaiting approval (step: {state.step})")
    if request.decision not in ApprovalDecision:
        return ApprovalRefusal(RefusalKind.INVALID, "decision must be 'approve' or 'reject'")
    if len(request.comment) > MAX_APPROVAL_COMMENT_LENGTH:
        return ApprovalRefusal(RefusalKind.INVALID, f"comment is longer than {MAX_APPROVAL_COMMENT_LENGTH} characters")
    return None
