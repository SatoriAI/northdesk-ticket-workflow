"""Worker-side log context: put ticket_id and workflow_id on every line about a ticket, including the SDK's own
lines (retries, failures) and those of libraries called from activities (httpx)."""

import logging

from temporalio import activity, workflow

from northdesk.constants import ticket_id_of


class TemporalContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        workflow_id = getattr(record, "workflow_id", None) or _workflow_id(record)
        if workflow_id:
            record.workflow_id = workflow_id
            ticket_id = ticket_id_of(workflow_id)
            if ticket_id is not None and not hasattr(record, "ticket_id"):
                record.ticket_id = ticket_id
        return True


def _workflow_id(record: logging.LogRecord) -> str | None:
    # The SDK's workflow and activity loggers attach these dicts to their records.
    for attribute in ("temporal_workflow", "temporal_activity"):
        details = getattr(record, attribute, None)
        workflow_id = details.get("workflow_id") if isinstance(details, dict) else None
        if isinstance(workflow_id, str):
            return workflow_id
    # Any other logger (e.g. httpx) called from inside an activity: the activity's context knows the workflow.
    if activity.in_activity():
        return activity.info().workflow_id
    return None


def install(root: logging.Logger) -> None:
    for handler in root.handlers:
        handler.addFilter(TemporalContextFilter())
    # The IDs are fields now; the SDK would otherwise also append a dict of them to every message.
    workflow.logger.workflow_info_on_message = False
    activity.logger.activity_info_on_message = False
