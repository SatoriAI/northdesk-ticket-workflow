import json
import logging

from northdesk.log import ContextLogFilter, JsonFormatter, bind_ticket, request_id_var, ticket_id_var


def record(**extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("northdesk.test", logging.INFO, __file__, 1, "ticket %s moved", ("T-2",), None)
    rec.__dict__.update(extra)
    return rec


def test_json_formatter__extras_become_top_level_fields() -> None:
    line = json.loads(JsonFormatter().format(record(ticket_id="T-2", workflow_id="ticket-T-2")))

    assert line["message"] == "ticket T-2 moved"
    assert (line["ticket_id"], line["workflow_id"], line["level"]) == ("T-2", "ticket-T-2", "INFO")


def test_context_filter__adds_request_and_bound_ticket() -> None:
    request_token, ticket_token = request_id_var.set("req-1"), ticket_id_var.set(None)
    try:
        bind_ticket("T-2")
        rec = record()
        ContextLogFilter().filter(rec)
    finally:
        ticket_id_var.reset(ticket_token)
        request_id_var.reset(request_token)

    assert (rec.request_id, rec.ticket_id, rec.workflow_id) == ("req-1", "T-2", "ticket-T-2")  # type: ignore[attr-defined]


def test_context_filter__keeps_ids_passed_explicitly() -> None:
    ticket_token = ticket_id_var.set("T-1")
    try:
        rec = record(ticket_id="T-2", workflow_id="ticket-T-2")
        ContextLogFilter().filter(rec)
    finally:
        ticket_id_var.reset(ticket_token)

    assert (rec.ticket_id, rec.workflow_id) == ("T-2", "ticket-T-2")  # type: ignore[attr-defined]
