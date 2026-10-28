"""The ticket workflow: deterministic orchestration and state. All I/O happens in activities.

load_context -> decide -> validate -> (refunds only) wait for approval -> act. Every path ends in exactly one
act, and each action type is carried out at most once per workflow, which is what keeps the idempotency key
"{workflow_id}:{action}" unique.
"""

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from northdesk.domain.models import (
        Action,
        ApprovalDecision,
        ApprovalRequest,
        Category,
        Command,
        HistoryEntry,
        Priority,
        Step,
        Ticket,
        TicketState,
    )
    from northdesk.domain.rules import validate
    from northdesk.workflows.activities import (
        UNKNOWN_CUSTOMER,
        ActInput,
        DecideInput,
        TicketActivities,
    )
    from northdesk.workflows.policies import (
        ACT_RETRY,
        ACT_TIMEOUT,
        APPROVAL_TIMEOUT,
        DECIDE_RETRY,
        DECIDE_TIMEOUT,
        LOAD_CONTEXT_RETRY,
        LOAD_CONTEXT_TIMEOUT,
    )

ACT_FAILED = "ActFailed"
MAX_APPROVAL_COMMENT_LENGTH = 500
REFUND_DECLINED_MESSAGE = (
    "We have reviewed your refund request and cannot approve it. "
    "If you think this is a mistake, reply to this message and we will take another look."
)


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        self._state = TicketState()
        self._ticket_id = ""

    @workflow.run
    async def run(self, ticket: Ticket) -> TicketState:
        self._ticket_id = ticket.ticket_id
        command, reason = await self._decide(ticket)
        if command.action == Action.REFUND:
            command, reason = await self._await_approval(command)
        self._state.command = command
        self._advance(Step.ACTING, reason)
        await self._act(command)
        self._advance(Step.COMPLETED, f"{command.action} done")
        return self._state

    @workflow.query
    def state(self) -> TicketState:
        return self._state

    @workflow.update
    def approval(self, request: ApprovalRequest) -> TicketState:
        self._state.approval = request
        return self._state

    @approval.validator
    def _check_approval(self, request: ApprovalRequest) -> None:
        """Runs before the Update is accepted; raising refuses it without touching the workflow's history."""
        if self._state.step != Step.AWAITING_APPROVAL or self._state.approval is not None:
            raise ValueError(f"ticket is not awaiting approval (step: {self._state.step})")
        if request.decision not in {ApprovalDecision.APPROVE, ApprovalDecision.REJECT}:
            raise ValueError("decision must be 'approve' or 'reject'")
        if len(request.comment) > MAX_APPROVAL_COMMENT_LENGTH:
            raise ValueError(f"comment is longer than {MAX_APPROVAL_COMMENT_LENGTH} characters")

    async def _decide(self, ticket: Ticket) -> tuple[Command, str]:
        """The command to carry out and the reason recorded for it: load the context, ask the agent, validate."""
        self._advance(Step.LOADING_CONTEXT, "ticket received")
        try:
            context = await workflow.execute_activity_method(
                TicketActivities.load_context,
                ticket,
                start_to_close_timeout=LOAD_CONTEXT_TIMEOUT,
                retry_policy=LOAD_CONTEXT_RETRY,
            )
        except ActivityError as exc:
            if _failure_type(exc) != UNKNOWN_CUSTOMER:  # cancellation or a broken data layer: not ours to hide
                raise
            return _escalation(Category.OTHER, Priority.NORMAL, "unknown customer")

        self._advance(Step.DECIDING, f"customer {context.customer.customer_id} loaded")
        try:
            decision = await workflow.execute_activity_method(
                TicketActivities.decide,
                DecideInput(ticket=ticket, context=context),
                start_to_close_timeout=DECIDE_TIMEOUT,
                retry_policy=DECIDE_RETRY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):  # the workflow itself was cancelled: stop, do not act
                raise
            # No usable decision within the loop cap (non-retryable), or the LLM kept failing until the retries
            # ran out: either way a person has to look at the ticket.
            return _escalation(Category.OTHER, Priority.NORMAL, f"the agent could not decide ({_failure_type(exc)})")
        self._state.decision = decision

        # The decision is untrusted model text; only validated values go into history and logs.
        self._advance(Step.VALIDATING, "agent proposed a decision")
        # Pure and deterministic, so it runs in the workflow itself, against the data load_context returned.
        validation = validate(decision, context)
        command = validation.command
        if validation.violation:
            return command, f"escalating: {validation.violation}"
        return command, f"validated {command.category}/{command.priority}/{command.action}"

    async def _await_approval(self, refund: Command) -> tuple[Command, str]:
        """Hold a valid refund until a person approves or rejects it, for at most APPROVAL_TIMEOUT."""
        self._state.command = refund
        self._advance(Step.AWAITING_APPROVAL, f"refund of {refund.amount_eur} EUR for {refund.invoice_id} proposed")
        try:
            await workflow.wait_condition(lambda: self._state.approval is not None, timeout=APPROVAL_TIMEOUT)
        except TimeoutError:
            hours = int(APPROVAL_TIMEOUT.total_seconds() // 3600)
            return _escalation(refund.category, refund.priority, f"refund not approved within {hours} h")
        approval = self._state.approval
        assert approval is not None  # the wait condition guarantees it  # noqa: S101
        comment = f": {approval.comment!r}" if approval.comment else ""
        if approval.decision == ApprovalDecision.APPROVE:
            return refund, f"refund approved{comment}"
        declined = Command(Action.REPLY, refund.category, refund.priority, message=REFUND_DECLINED_MESSAGE)
        return declined, f"refund rejected{comment}; telling the customer"

    async def _act(self, command: Command) -> None:
        key = f"{workflow.info().workflow_id}:{command.action.value}"
        try:
            self._state.result = await workflow.execute_activity_method(
                TicketActivities.act,
                ActInput(ticket_id=self._ticket_id, command=command, idempotency_key=key),
                start_to_close_timeout=ACT_TIMEOUT,
                retry_policy=ACT_RETRY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            # Every attempt may have been performed and its response lost, so the outcome is unknown: falling back
            # to another action could double up. Fail the workflow and say where to look.
            reason = f"{command.action} failed ({_failure_type(exc)}); outcome unknown, check helpdesk key {key}"
            self._advance(Step.FAILED, reason)
            raise ApplicationError(reason, type=ACT_FAILED, non_retryable=True) from exc

    def _advance(self, step: Step, reason: str) -> None:
        self._state.step = step
        self._state.history.append(HistoryEntry(step=step, reason=reason))
        workflow.logger.info(
            "ticket %s: %s",
            step,
            reason,
            extra={"ticket_id": self._ticket_id, "workflow_id": workflow.info().workflow_id},
        )


def _escalation(category: Category, priority: Priority, reason: str) -> tuple[Command, str]:
    return Command(Action.ESCALATE, category, priority, reason=reason), f"escalating: {reason}"


def _failure_type(exc: ActivityError) -> str:
    return exc.cause.type if isinstance(exc.cause, ApplicationError) and exc.cause.type else type(exc.cause).__name__
