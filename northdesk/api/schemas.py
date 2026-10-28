from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from northdesk.workflows.ticket_workflow import MAX_APPROVAL_COMMENT_LENGTH

TICKET_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=TICKET_ID_PATTERN)
    customer_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(max_length=500)
    body: str = Field(max_length=20_000)


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=MAX_APPROVAL_COMMENT_LENGTH)


class ApprovalOut(BaseModel):
    decision: str
    comment: str


class TicketAccepted(BaseModel):
    ticket_id: str
    workflow_id: str


class DecisionOut(BaseModel):
    """Both the agent's proposal and the validated command have this shape."""

    category: str
    priority: str
    action: str
    message: str | None
    reason: str | None
    kb_article_ids: list[str]
    invoice_id: str | None
    # The proposal is model output kept for the record; showing it must never fail, even for a "NaN".
    amount_eur: Annotated[Decimal, Field(allow_inf_nan=True)] | None


class ActionResultOut(BaseModel):
    action: str
    helpdesk_action_id: str
    idempotency_key: str
    replayed: bool


class HistoryEntryOut(BaseModel):
    step: str
    reason: str


class TicketStateOut(BaseModel):
    ticket_id: str
    workflow_id: str
    step: str
    decision: DecisionOut | None
    command: DecisionOut | None
    approval: ApprovalOut | None
    result: ActionResultOut | None
    history: list[HistoryEntryOut]
