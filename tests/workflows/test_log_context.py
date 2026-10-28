import dataclasses
import logging

from temporalio import activity
from temporalio.testing import ActivityEnvironment

from northdesk.workflows.log_context import TemporalContextFilter


def filtered(rec: logging.LogRecord) -> logging.LogRecord:
    TemporalContextFilter().filter(rec)
    return rec


def record(**extra: object) -> logging.LogRecord:
    rec = logging.LogRecord(
        "temporalio.activity", logging.WARNING, __file__, 1, "Completing activity as failed", (), None
    )
    rec.__dict__.update(extra)
    return rec


def test_filter__lifts_ids_from_the_sdk_activity_details() -> None:
    rec = filtered(record(temporal_activity={"workflow_id": "ticket-T-2", "attempt": 3}))

    assert (rec.workflow_id, rec.ticket_id) == ("ticket-T-2", "T-2")  # type: ignore[attr-defined]


def test_filter__lifts_ids_from_the_sdk_workflow_details() -> None:
    rec = filtered(record(temporal_workflow={"workflow_id": "ticket-T-7"}))

    assert rec.ticket_id == "T-7"  # type: ignore[attr-defined]


async def test_filter__other_libraries_inside_an_activity_get_its_workflow() -> None:
    """e.g. httpx's "HTTP Request: POST .../actions" line, logged while act runs."""
    captured: list[logging.LogRecord] = []

    @activity.defn
    async def act_like() -> None:
        captured.append(filtered(logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request", (), None)))

    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id="ticket-T-4")
    await env.run(act_like)

    assert (captured[0].workflow_id, captured[0].ticket_id) == ("ticket-T-4", "T-4")  # type: ignore[attr-defined]


def test_filter__leaves_unrelated_lines_alone() -> None:
    rec = filtered(logging.LogRecord("uvicorn", logging.INFO, __file__, 1, "started", (), None))

    assert not hasattr(rec, "workflow_id")
