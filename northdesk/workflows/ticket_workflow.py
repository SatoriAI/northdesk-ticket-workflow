"""The ticket workflow: deterministic orchestration and state. All I/O happens in activities.

load_context -> decide -> validate -> (refunds only) wait for approval -> act. Every path ends in exactly one
act, and each action type is carried out at most once per workflow, which is what keeps the idempotency key
"{workflow_id}:{action}" unique.
"""

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from northdesk.domain.approval import RefusalKind, approval_refusal
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
        HELPDESK_REJECTED,
        UNKNOWN_CUSTOMER,
        ActInput,
        DecideInput,
        TicketActivities,
    )
    from northdesk.workflows.contract import (
        APPROVAL_UPDATE,
        INVALID_APPROVAL,
        NOT_AWAITING_APPROVAL,
        STATE_QUERY,
        TICKET_FAILED,
        WORKFLOW_NAME,
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

REFUND_DECLINED_MESSAGE = (
    "We have reviewed your refund request and cannot approve it. "
    "If you think this is a mistake, reply to this message and we will take another look."
)


@workflow.defn(name=WORKFLOW_NAME)
class TicketWorkflow:
    def __init__(self) -> None:
        self._state = TicketState()
        self._ticket_id = ""

    @workflow.run
    async def run(self, ticket: Ticket) -> TicketState:
        self._ticket_id = ticket.ticket_id
        try:
            command, reason = await self._decide(ticket)
            if command.action == Action.REFUND:
                command, reason = await self._await_approval(command)
            self._state.command = command
            self._advance(Step.ACTING, reason)
            await self._act(command)
        except ActivityError as exc:
            if is_cancelled_exception(exc):  # the workflow itself was cancelled: stop, record nothing
                raise
            # Any other failure that reaches here ends the ticket: say where it stopped and why, then fail.
            reason = self._failure_reason(exc)
            self._advance(Step.FAILED, reason)
            raise ApplicationError(reason, type=TICKET_FAILED, non_retryable=True) from exc
        self._advance(Step.COMPLETED, f"{command.action} done")
        return self._state

    @workflow.query(name=STATE_QUERY)
    def state(self) -> TicketState:
        return self._state

    @workflow.update(name=APPROVAL_UPDATE)
    def approval(self, request: ApprovalRequest) -> TicketState:
        # Synchronous on purpose: the SDK runs it straight after its validator, with no await in between, so two
        # answers sent at once cannot both pass the "not answered yet" check. It returns the state at the moment
        # the answer is accepted (step still awaiting_approval). An async handler that waited for the next step
        # was tried and changes the recorded history (replay fails), so it would break approvals in flight.
        self._state.approval = request
        return self._state

    @approval.validator
    def _check_approval(self, request: ApprovalRequest) -> None:
        """Runs before the Update is accepted; raising refuses it without touching the workflow's history."""
        refusal = approval_refusal(self._state, request)
        if refusal is not None:
            kind = NOT_AWAITING_APPROVAL if refusal.kind == RefusalKind.NOT_AWAITING else INVALID_APPROVAL
            raise ApplicationError(refusal.reason, type=kind)

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
            if _failure_type(exc) != UNKNOWN_CUSTOMER:  # cancellation or a broken data layer: run() handles it
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
        self._state.result = await workflow.execute_activity_method(
            TicketActivities.act,
            ActInput(ticket_id=self._ticket_id, command=command, idempotency_key=self._key(command)),
            start_to_close_timeout=ACT_TIMEOUT,
            retry_policy=ACT_RETRY,
        )

    def _failure_reason(self, exc: ActivityError) -> str:
        failure = _failure_type(exc)
        command = self._state.command
        if self._state.step != Step.ACTING or command is None:
            return f"{self._state.step} failed ({failure})"
        # A fallback action could double up with one already done, so the ticket fails and says where to look.
        if failure == HELPDESK_REJECTED:  # a 4xx: nothing was done, or the key already holds another request
            return f"{command.action} rejected by the helpdesk; check helpdesk key {self._key(command)}"
        # Out of attempts: any of them may have been performed with its response lost.
        return f"{command.action} failed ({failure}); outcome unknown, check helpdesk key {self._key(command)}"

    @staticmethod
    def _key(command: Command) -> str:
        return f"{workflow.info().workflow_id}:{command.action.value}"

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
