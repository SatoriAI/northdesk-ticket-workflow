"""Replays a recorded workflow history against today's code. A change that would alter what a running workflow
already did (for example a validation rule that now escalates the refund T-2 is waiting on) fails here with a
nondeterminism error; such changes need workflow.patched() so in-flight tickets keep their old path."""

from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from northdesk.workflows.converter import DATA_CONVERTER
from northdesk.workflows.ticket_workflow import TicketWorkflow

HISTORIES = Path(__file__).parent / "histories"


async def test_replay__t2_waiting_then_approved_still_replays() -> None:
    history = WorkflowHistory.from_json("ticket-T-2", (HISTORIES / "ticket-T-2-approved.json").read_text())

    await Replayer(workflows=[TicketWorkflow], data_converter=DATA_CONVERTER).replay_workflow(history)
