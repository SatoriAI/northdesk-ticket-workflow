"""The ticket workflow: deterministic orchestration and state. All I/O happens in activities."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from northdesk.domain.models import Action, Command, HistoryEntry, Step, Ticket, TicketState
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
        decision = await workflow.execute_activity_method(
            TicketActivities.decide,
            DecideInput(ticket=ticket, context=context),
            start_to_close_timeout=DECIDE_TIMEOUT,
            retry_policy=DECIDE_RETRY,
        )
        self._state.decision = decision

        # The decision is untrusted model text; only validated values go into history and logs.
        self._advance(Step.VALIDATING, "agent proposed a decision")
        # Pure and deterministic, so it runs in the workflow itself, against the data load_context returned.
        validation = validate(decision, context)
        command = validation.command
        if validation.violation:
            acting_reason = f"escalating: {validation.violation}"
        elif command.action == Action.REFUND:
            # Refunds need human approval, which the next step of this workflow adds. Until it exists a refund
            # is escalated, so no refund is ever carried out unapproved.
            command = Command(
                Action.ESCALATE, command.category, command.priority, reason="refund awaits human approval"
            )
            acting_reason = "escalating: refund approval is not implemented yet"
        else:
            acting_reason = f"validated {command.category}/{command.priority}/{command.action}"
        self._state.command = command

        self._advance(Step.ACTING, acting_reason)
        self._state.result = await workflow.execute_activity_method(
            TicketActivities.act,
            ActInput(ticket_id=ticket.ticket_id, command=command),
            start_to_close_timeout=ACT_TIMEOUT,
            retry_policy=ACT_RETRY,
        )

        self._advance(Step.COMPLETED, f"{command.action} done")
        return self._state

    @workflow.query
    def state(self) -> TicketState:
        return self._state

    def _advance(self, step: Step, reason: str) -> None:
        self._state.step = step
        self._state.history.append(HistoryEntry(step=step, reason=reason))
        workflow.logger.info(
            "ticket %s: %s",
            step,
            reason,
            extra={"ticket_id": self._ticket_id, "workflow_id": workflow.info().workflow_id},
        )
