"""What a client needs to talk to TicketWorkflow without importing it (and, through it, the worker's code).

The workflow declares these names explicitly; they equal the defaults Temporal derived before, so recorded
histories still replay.
"""

WORKFLOW_NAME = "TicketWorkflow"
STATE_QUERY = "state"
APPROVAL_UPDATE = "approval"

# Failure types a client can act on.
NOT_AWAITING_APPROVAL = "NotAwaitingApproval"  # the approval Update was refused: wrong step or already answered
INVALID_APPROVAL = "InvalidApproval"  # the approval Update was refused: malformed answer
TICKET_FAILED = "TicketFailed"  # the workflow failed; its state's last history entry says why
