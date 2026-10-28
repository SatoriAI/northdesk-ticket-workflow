"""The ticket workflow: deterministic orchestration and state. All I/O happens in activities."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from northdesk.domain.models import HistoryEntry, Step, Ticket, TicketState
    from northdesk.workflows.activities import ActInput, DecideInput, TicketActivities
    from northdesk.workflows.policies import (
        ACT_RETRY,
        ACT_TIMEOUT,
        DECIDE_RETRY,
        DECIDE_TIMEOUT,
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
            TicketActivities.load_context, ticket.customer_id, start_to_close_timeout=LOAD_CONTEXT_TIMEOUT
        )

        self._advance(Step.DECIDING, f"customer {context.customer.customer_id} loaded")
        decision = await workflow.execute_activity_method(
            TicketActivities.decide,
            DecideInput(ticket=ticket, context=context),
            start_to_close_timeout=DECIDE_TIMEOUT,
            retry_policy=DECIDE_RETRY,
        )
        self._state.decision = decision

        self._advance(Step.ACTING, f"decided {decision.category}/{decision.priority}/{decision.action}")
        self._state.result = await workflow.execute_activity_method(
            TicketActivities.act,
            ActInput(ticket_id=ticket.ticket_id, decision=decision),
            start_to_close_timeout=ACT_TIMEOUT,
            retry_policy=ACT_RETRY,
        )

        self._advance(Step.COMPLETED, f"{decision.action} done")
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
