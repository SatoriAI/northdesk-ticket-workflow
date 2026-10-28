"""The ticket workflow: deterministic orchestration and state. All I/O happens in activities."""

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from northdesk.domain.models import (
        Action,
        Category,
        Command,
        Decision,
        HistoryEntry,
        Priority,
        Step,
        Ticket,
        TicketContext,
        TicketState,
    )
    from northdesk.domain.rules import validate
    from northdesk.workflows.activities import ActInput, DecideInput, TicketActivities
    from northdesk.workflows.policies import (
        ACT_RETRY,
        ACT_TIMEOUT,
        DECIDE_RETRY,
        DECIDE_TIMEOUT,
        LOAD_CONTEXT_RETRY,
        LOAD_CONTEXT_TIMEOUT,
    )


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        self._state = TicketState()
        self._ticket_id = ""

    @workflow.run
    async def run(self, ticket: Ticket) -> TicketState:
        self._ticket_id = ticket.ticket_id
        self._advance(Step.LOADING_CONTEXT, "ticket received")
        context = await workflow.execute_activity_method(
            TicketActivities.load_context,
            ticket,
            start_to_close_timeout=LOAD_CONTEXT_TIMEOUT,
            retry_policy=LOAD_CONTEXT_RETRY,
        )

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
            failure = exc.cause.type if isinstance(exc.cause, ApplicationError) else type(exc.cause).__name__
            reason = f"the agent could not decide ({failure})"
            command = Command(Action.ESCALATE, Category.OTHER, Priority.NORMAL, reason=reason)
            acting_reason = f"escalating: {reason}"
        else:
            self._state.decision = decision
            # The decision is untrusted model text; only validated values go into history and logs.
            self._advance(Step.VALIDATING, "agent proposed a decision")
            command, acting_reason = self._validated(decision, context)
        self._state.command = command

        self._advance(Step.ACTING, acting_reason)
        self._state.result = await workflow.execute_activity_method(
            TicketActivities.act,
            # One act per action type per workflow, so this key is unique, and stable across every retry.
            ActInput(
                ticket_id=ticket.ticket_id,
                command=command,
                idempotency_key=f"{workflow.info().workflow_id}:{command.action.value}",
            ),
            start_to_close_timeout=ACT_TIMEOUT,
            retry_policy=ACT_RETRY,
        )

        self._advance(Step.COMPLETED, f"{command.action} done")
        return self._state

    @workflow.query
    def state(self) -> TicketState:
        return self._state

    @staticmethod
    def _validated(decision: Decision, context: TicketContext) -> tuple[Command, str]:
        """The command to carry out and the reason recorded for it."""
        # Pure and deterministic, so it runs in the workflow itself, against the data load_context returned.
        validation = validate(decision, context)
        command = validation.command
        if validation.violation:
            return command, f"escalating: {validation.violation}"
        if command.action == Action.REFUND:
            # Refunds need human approval, which the next step of this workflow adds. Until it exists a refund
            # is escalated, so no refund is ever carried out unapproved.
            held = Command(Action.ESCALATE, command.category, command.priority, reason="refund awaits human approval")
            return held, "escalating: refund approval is not implemented yet"
        return command, f"validated {command.category}/{command.priority}/{command.action}"

    def _advance(self, step: Step, reason: str) -> None:
        self._state.step = step
        self._state.history.append(HistoryEntry(step=step, reason=reason))
        workflow.logger.info(
            "ticket %s: %s",
            step,
            reason,
            extra={"ticket_id": self._ticket_id, "workflow_id": workflow.info().workflow_id},
        )
